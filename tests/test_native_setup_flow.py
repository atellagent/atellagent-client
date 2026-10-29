"""Native setup progresses without silently approving, consenting or replacing keys."""

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from atellagent_client.onboarding import native_setup_flow
from atellagent_client.onboarding.device_identity import AdmittedDevice
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_credential_store import NativeCredentialStore
from atellagent_client.onboarding.native_setup_flow import NativeSetupFlow
from atellagent_client.onboarding.setup_keys import SetupKeys
from native_credential_support import stored_case


@pytest.fixture
def flow(tmp_path, monkeypatch):
    case = stored_case(tmp_path)
    draft = case.draft
    setup = SimpleNamespace(kind="device", operation_id=draft.setup_id, platform_origin=draft.platform_origin,
        tenant_id=draft.tenant_id, owner_user_id=draft.owner_user_id, keys=SetupKeys.generate())
    receipt = SimpleNamespace(kind="device", id=draft.setup_id, device_id=draft.device_id)
    device = AdmittedDevice(draft.tenant_id, draft.device_id, draft.owner_user_id, setup.keys.proof_key,
                            tmp_path / "device.pem", tmp_path / "device-key.pem")
    state = SimpleNamespace(assignment=replace(case.assignment, owner_consented_at=datetime.now(timezone.utc)),
        delivery=case.credential, calls=[], setup=setup, receipt=receipt, case=case)
    async def assignment(request_id):
        assert request_id == draft.proposal.id
        state.calls.append("assignment")
        return state.assignment
    client = SimpleNamespace(device=device, assignment=assignment)
    async def trust(origin):
        assert origin == draft.platform_origin
        state.calls.append("trust")
        return case.trust
    class Claim:
        def __init__(self, **kwargs):
            assert kwargs == dict(origin=case.trust.service_account_api_origin, device=device, setup_id=draft.setup_id)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            state.calls.append("claim_closed")
        async def stage(self, **kwargs):
            assert kwargs["request_id"] == draft.proposal.id
            assert kwargs["intent_sha256"] == state.assignment.intent_sha256
            state.calls.append("stage")
        async def certificate(self, **kwargs):
            assert kwargs["assignment"] == state.assignment
            state.calls.append("certificate")
            return state.delivery
    monkeypatch.setattr(native_setup_flow, "fetch_service_account_trust", trust)
    monkeypatch.setattr(native_setup_flow, "NativeClaimClient", Claim)
    state.store = NativeCredentialStore(tmp_path / "credentials")
    state.flow = NativeSetupFlow(draft=draft, draft_store=case.draft_store, credential_store=state.store,
        installation=SimpleNamespace(load=lambda: (setup, receipt, "a" * 64)), device_client=client)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("status,consented,stage", [("pending", False, "awaiting_approval"),
    ("pending", True, "awaiting_approval"), ("approved", False, "awaiting_owner_consent")])
async def test_waits_without_claiming_or_implicitly_consenting(flow, status, consented, stage):
    flow.assignment = replace(flow.assignment, status=status,
                              owner_consented_at=flow.assignment.owner_consented_at if consented else None)
    assert (await flow.flow.advance()).stage == stage
    assert flow.calls == ["assignment"]
    assert list(flow.store.directory.iterdir()) == []


@pytest.mark.asyncio
async def test_approved_and_consented_stages_original_key_then_returns_progress(flow):
    flow.assignment = replace(flow.assignment, status="approved")
    assert (await flow.flow.advance()).stage == "issuing"
    assert flow.calls == ["assignment", "trust", "stage", "claim_closed"]
    assert list(flow.store.directory.iterdir()) == []


@pytest.mark.asyncio
async def test_pending_delivery_is_not_credential_activation(flow):
    flow.assignment = replace(flow.assignment, status="issuing")
    flow.delivery = None
    result = await flow.flow.advance()
    assert result.stage == "issuing" and result.activation is None
    assert list(flow.store.directory.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["denied", "cancelled", "expired", "failed"])
async def test_terminal_assignment_cannot_resume_claim(flow, status):
    flow.assignment = replace(flow.assignment, status=status)
    with pytest.raises(OnboardingClientError, match="onboarding_native_setup_" + status):
        await flow.flow.advance()
    assert flow.calls == ["assignment"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "intent", "setup", "device_key", "missing_consent"])
async def test_wrong_assignment_or_original_device_is_not_used(flow, change):
    if change == "missing":
        flow.assignment = None
    elif change == "intent":
        flow.assignment = replace(flow.assignment, intent_sha256="a" * 64)
    elif change == "setup":
        flow.assignment = replace(flow.assignment, onboarding_session_id=uuid4())
    elif change == "device_key":
        flow.setup.keys = SetupKeys.generate()
    else:
        flow.assignment = replace(flow.assignment, owner_consented_at=None)
    with pytest.raises(OnboardingClientError):
        await flow.flow.advance()
    assert "trust" not in flow.calls


@pytest.mark.asyncio
async def test_complete_issuance_activation_and_installed_recovery_use_same_selection(flow, monkeypatch):
    authorization_id = uuid4()
    factory = httpx.AsyncClient
    def handler(request):
        flow.calls.append("activate")
        assert "Authorization" not in request.headers
        # The real activation transport may only run after this durable commit.
        assert flow.store.load_selected(draft=flow.case.draft, assignment=flow.assignment,
            trust=flow.case.trust, draft_store=flow.case.draft_store).credential == flow.case.credential
        return httpx.Response(200, json=dict(operation_id=str(flow.case.credential.operation_id),
            service_account_id=str(flow.assignment.service_account_id), integration_id=str(flow.assignment.integration_id),
            authorization_id=str(authorization_id), authorization_epoch=1, status="credential_active",
            runtime_status="awaiting_registration"))
    monkeypatch.setattr("atellagent_client.onboarding.service_account_http.httpx.AsyncClient",
                        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(handler)))
    first = await flow.flow.advance()
    assert first.stage == "awaiting_registration" and first.activation.authorization_id == authorization_id
    flow.assignment = replace(flow.assignment, status="installed")
    flow.calls.clear()
    assert (await flow.flow.advance()).activation == first.activation
    assert flow.calls == ["assignment", "trust", "activate"]
    assert len(list(flow.store.directory.glob("integration-*"))) == 1


@pytest.mark.asyncio
async def test_installed_without_local_selection_requires_recovery_not_new_claim(flow):
    flow.assignment = replace(flow.assignment, status="installed")
    with pytest.raises(OnboardingClientError, match="onboarding_native_credential_recovery_required"):
        await flow.flow.advance()
    assert flow.calls == ["assignment", "trust"]
