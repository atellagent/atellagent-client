"""Fresh CLI setup saves keys before POST and resumes lost replies without user auth."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
import httpx
import pytest
from atellagent_client.cli.main import _async_main
from atellagent_client.cli import workload_request
from atellagent_client.onboarding.device_login_models import ManagementTokens
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.workload_context import WorkloadContext
from test_workload_management import receipt as receipt_payload


@pytest.fixture
def submission(tmp_path, monkeypatch):
    context = WorkloadContext(tmp_path / "workload")
    owner, tenant = uuid4(), uuid4()
    login = AsyncMock(return_value=ManagementTokens(300, "fixture-human-token", None))
    monkeypatch.setattr(workload_request, "login", login)
    monkeypatch.setattr("builtins.input", lambda _: "1")
    state = SimpleNamespace(calls=[], draft=None, ack=None, failure=None)
    def handler(request):
        state.calls.append(request)
        if request.url.path.endswith("/identity"):
            assert request.headers["Authorization"] == "Bearer fixture-human-token"
            return httpx.Response(200, json={"user_id": str(owner), "selection_required": True,
                "memberships": [{"tenant_id": str(tenant), "tenant_name": "Example", "can_approve": False}]})
        if request.url.path == "/api/client-onboarding/sessions":
            body = json.loads(request.content)
            setup = context.setups.load(UUID(body["proposal"]["id"]), expected_origin="https://platform.example",
                                        expected_tenant_id=tenant, expected_owner_user_id=owner)
            assert setup.proposal() == body["proposal"] and body["proof"]["signature"]
            if state.failure == "bootstrap":
                state.failure = None
                raise httpx.ReadError("lost bootstrap reply", request=request)
            return httpx.Response(200, json={"id": str(setup.operation_id), "kind": "workload", "status": "pending",
                "device_id": None, "revision": 1, "expires_at": "2026-10-08T12:00:00Z", "claim_expires_at": None})
        if request.url.path.endswith("/workload"):
            body = json.loads(request.content)
            state.draft = context.drafts.load(UUID(body["proposal"]["id"]))
            assert state.draft.proposal.payload() == body["proposal"] and body["proof"]["signature"]
            assert context.local_inputs.load(state.draft).handler == "customer.model:Handler"
            state.ack = receipt_payload(state.draft)
            if state.failure == "workload":
                state.failure = None
                raise httpx.ReadError("lost workload reply", request=request)
            return httpx.Response(200, json=state.ack)
        assert request.url.path.endswith("/status")
        assert "Authorization" not in request.headers and "X-Atellagent-Onboarding-Signature" in request.headers
        if state.ack is None:
            return httpx.Response(403, json={"error": {"code": "onboarding_permission_denied"}})
        return httpx.Response(200, json=state.ack)
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.setup_management.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(handler)))
    arguments = ["workload", "request", "--state-directory", str(context.directory), "--platform-url", "https://platform.example",
        "--name", "Customer model", "--type", "model", "--capability", "model.inference", "--handler", "customer.model:Handler",
        "--target-idempotent", "--wait-seconds", "1"]
    return context, state, login, arguments


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_reply", [False, True])
async def test_attended_request_then_proof_only_resume(submission, lost_reply, capsys):
    context, state, login, arguments = submission
    if lost_reply:
        state.failure = "workload"
        with pytest.raises(OnboardingClientError, match="transport_unavailable"): await _async_main(arguments)
    else: await _async_main(arguments)
    draft = state.draft
    original = draft.encode()
    state.calls.clear()
    await _async_main(["workload", "resume", str(draft.proposal.id), "--state-directory", str(context.directory), "--wait-seconds", "1"])
    assert login.await_count == 1 and all(request.url.path.endswith("/status") for request in state.calls)
    assert context.drafts.load(draft.proposal.id).encode() == original
    assert context.receipts.load(draft).service_account_id == UUID(state.ack["service_account_id"])
    assert "Workload request saved:" in capsys.readouterr().out
    for record in context.directory.rglob("*.json"):
        assert b"fixture-human-token" not in record.read_bytes()


@pytest.mark.asyncio
async def test_explicit_resubmission_after_bootstrap_reply_loss_retains_keys(submission):
    context, state, login, arguments = submission
    state.failure = "bootstrap"
    with pytest.raises(OnboardingClientError, match="transport_unavailable"): await _async_main(arguments)
    paths = [path for path in context.drafts.directory.glob("*.json") if not path.name.endswith("-local.json")]
    assert len(paths) == 1
    identifier = UUID(paths[0].stem)
    before = context.drafts.load(identifier).encode()
    await _async_main(["workload", "submit", str(identifier), "--state-directory", str(context.directory), "--wait-seconds", "1"])
    assert context.drafts.load(identifier).encode() == before and login.await_count == 2
    assert state.ack is not None
