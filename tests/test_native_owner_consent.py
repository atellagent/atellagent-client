"""Explicit exact-owner consent never doubles as provisioning approval."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from uuid import uuid4

import httpx
import pytest

from atellagent_client.onboarding.device_login_models import ManagementTokens
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_owner_consent import NativeConsentReceipt
from atellagent_client.onboarding.setup_management import SetupManagementClient
from native_credential_support import stored_case


@pytest.fixture
def case(tmp_path):
    item = stored_case(tmp_path)
    item.assignment = replace(item.assignment, status="approved")
    return item


def request_args(case):
    return dict(draft=case.draft, store=case.draft_store, assignment=case.assignment,
                reviewed_intent_sha256=case.assignment.intent_sha256)


def ack(case):
    return {"id": str(case.assignment.request_id), "status": "consented", "revision": 1,
            "consented_at": datetime.now(timezone.utc).isoformat()}


def transport(monkeypatch, case, *, different_owner=False, fresh_auth_required=False):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer management-test"
        if request.method == "GET":
            return httpx.Response(200, json={"user_id": str(uuid4() if different_owner else case.draft.owner_user_id),
                "selection_required": True, "memberships": [{"tenant_id": str(case.draft.tenant_id),
                    "tenant_name": "Customer", "can_approve": False}]})
        assert request.url.path == f"/api/client-onboarding/owner-consents/{case.assignment.request_id}"
        assert json.loads(request.content) == {"tenant_id": str(case.draft.tenant_id), "revision": 1,
                                              "intent_sha256": case.assignment.intent_sha256}
        if fresh_auth_required:
            return httpx.Response(403, json={"error": {"code": "onboarding_authentication_required"}})
        return httpx.Response(200, json=ack(case))
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.setup_management.httpx.AsyncClient", lambda **kwargs:
        factory(**kwargs, transport=httpx.MockTransport(handler)))
    return calls


@pytest.mark.asyncio
async def test_exact_reviewed_consent_is_a_separate_attended_operation(case, monkeypatch):
    calls = transport(monkeypatch, case)
    async with SetupManagementClient(origin=case.draft.platform_origin, tokens=ManagementTokens(300, "management-test", None)) as client:
        result = await client.consent_native(**request_args(case))
        assert result.request_id == case.assignment.request_id
        assert not hasattr(result, "authorization_id")
    assert [call.method for call in calls] == ["GET", "POST"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["review", "assignment", "stage"])
async def test_changed_reviewed_intent_or_stage_never_enters_management_network(case, monkeypatch, changed):
    calls = transport(monkeypatch, case)
    kwargs = request_args(case)
    if changed == "review":
        kwargs["reviewed_intent_sha256"] = "a" * 64
    else:
        kwargs["assignment"] = replace(case.assignment, **({"intent_sha256": "a" * 64} if changed == "assignment" else {"status": "issued"}))
    async with SetupManagementClient(origin=case.draft.platform_origin, tokens=ManagementTokens(300, "management-test", None)) as client:
        with pytest.raises(OnboardingClientError):
            await client.consent_native(**kwargs)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["owner", "fresh_auth"])
async def test_wrong_owner_or_stale_auth_does_not_retry_or_auto_login(case, monkeypatch, failure):
    calls = transport(monkeypatch, case, different_owner=failure == "owner", fresh_auth_required=failure == "fresh_auth")
    async with SetupManagementClient(origin=case.draft.platform_origin, tokens=ManagementTokens(300, "management-test", None)) as client:
        with pytest.raises(OnboardingClientError) as error:
            await client.consent_native(**request_args(case))
        assert error.value.code == ("onboarding_account_changed" if failure == "owner" else "onboarding_authentication_required")
        assert error.value.retryable is False
    assert len(calls) == (1 if failure == "owner" else 2)


@pytest.mark.parametrize("change", [{"id": str(uuid4())}, {"revision": True}, {"status": "approved"},
    {"consented_at": "2026-10-01T00:00:00"}, {"authorization_id": str(uuid4())}])
def test_acknowledgement_is_consent_only(case, change):
    with pytest.raises(OnboardingClientError):
        NativeConsentReceipt.from_payload({**ack(case), **change}, assignment=case.assignment)
