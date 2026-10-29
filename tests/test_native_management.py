"""Attended submission retains its saved intent; approval and consent stay separate."""

import base64
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from atellagent_client.onboarding.device_login_models import ManagementTokens
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_setup_store import NativeSetupStore
from atellagent_client.onboarding.native_request_receipt import NativeRequestReceipt
from atellagent_client.onboarding.setup_keys import SetupKeys
from atellagent_client.onboarding.setup_management import SetupManagementClient
from test_native_setup_store import draft


@pytest.fixture
def case(tmp_path, draft):
    store = NativeSetupStore(tmp_path / "native")
    device_setup = SimpleNamespace(platform_origin=draft.platform_origin, kind="device",
        operation_id=draft.setup_id, tenant_id=draft.tenant_id, owner_user_id=draft.owner_user_id,
        keys=SetupKeys.generate())
    receipt = SimpleNamespace(kind="device", id=draft.setup_id, device_id=draft.device_id)
    return SimpleNamespace(draft=draft, store=store, device_setup=device_setup,
        installation=SimpleNamespace(load=lambda: (device_setup, receipt, "a" * 64)),
        response={"id": str(draft.proposal.id), "kind": "native_connection", "status": "pending",
            "device_id": str(draft.device_id), "revision": 1, "intent_sha256": "a" * 64,
            "service_account_id": str(uuid4()), "integration_id": str(uuid4()),
            "expires_at": "2026-10-08T12:00:00Z", "claim_expires_at": None})


@pytest.mark.asyncio
async def test_submission_requires_saved_draft_and_reuses_exact_proof_boundary(case, monkeypatch):
    calls, nonces = [], []
    def handler(request):
        assert request.headers["Authorization"] == "Bearer human-access"
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"user_id": str(case.draft.owner_user_id), "selection_required": True,
                "memberships": [{"tenant_id": str(case.draft.tenant_id), "tenant_name": "Customer", "can_approve": False}]})
        payload = json.loads(request.content)
        assert set(payload) == {"tenant_id", "proposal", "proof", "owner_consent"}
        assert payload["owner_consent"] is True
        assert payload["proposal"] == case.draft.proposal.payload()
        assert "tls_key_pem" not in str(payload) and "PRIVATE KEY" not in str(payload)
        assert request.url.path == f"/api/client-onboarding/sessions/{case.draft.setup_id}/connections"
        body = {"tenant_id": payload["tenant_id"], "owner_user_id": str(case.draft.owner_user_id),
                "proposal": payload["proposal"], "owner_consent": True}
        proof = payload["proof"]
        material = "\n".join(("atellagent-onboarding-request-v1", str(case.draft.setup_id), "POST", request.url.path,
            sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            proof["timestamp"], proof["nonce"])).encode()
        case.device_setup.keys.proof_key.public_key().verify(base64.urlsafe_b64decode(proof["signature"] + "=="), material)
        nonces.append(proof["nonce"])
        return httpx.Response(200, json=case.response)
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.setup_management.httpx.AsyncClient", lambda **kwargs:
        factory(**kwargs, transport=httpx.MockTransport(handler)))
    async with SetupManagementClient(origin=case.draft.platform_origin, tokens=ManagementTokens(300, "human-access", None)) as client:
        with pytest.raises(OnboardingClientError):
            await client.submit_native(draft=case.draft, store=case.store, installation=case.installation)
        assert calls == []
        case.store.save_initial(case.draft)
        for _ in range(2):
            receipt = await client.submit_native(draft=case.draft, store=case.store, installation=case.installation)
            assert receipt.status == "pending" and receipt.claim_expires_at is None
    assert calls == ["GET", "POST", "GET", "POST"] and len(set(nonces)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["account", "membership", "installation"])
async def test_changed_account_membership_or_installation_never_submits(case, monkeypatch, change):
    case.store.save_initial(case.draft)
    submitted = []
    def handler(request):
        if request.method != "GET":
            submitted.append(request)
        return httpx.Response(200, json={"user_id": str(uuid4() if change == "account" else case.draft.owner_user_id),
            "selection_required": True, "memberships": [] if change == "membership" else [
                {"tenant_id": str(case.draft.tenant_id), "tenant_name": "Customer", "can_approve": False}]})
    if change == "installation":
        case.device_setup.operation_id = uuid4()
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.setup_management.httpx.AsyncClient", lambda **kwargs:
        factory(**kwargs, transport=httpx.MockTransport(handler)))
    async with SetupManagementClient(origin=case.draft.platform_origin, tokens=ManagementTokens(300, "human-access", None)) as client:
        with pytest.raises(OnboardingClientError):
            await client.submit_native(draft=case.draft, store=case.store, installation=case.installation)
    assert submitted == []


@pytest.mark.parametrize("change", [{"id": str(uuid4())}, {"device_id": str(uuid4())},
    {"kind": "workload"}, {"revision": True}, {"status": "ready"}, {"intent_sha256": "bad"},
    {"expires_at": "2026-10-08T12:00:00"}, {"enrollment_token": "obsolete"}])
def test_receipt_never_relabels_or_claims_readiness(case, change):
    with pytest.raises(OnboardingClientError):
        NativeRequestReceipt.from_payload({**case.response, **change}, draft=case.draft)
