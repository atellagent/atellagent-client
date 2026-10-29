"""Full public setup composition retains keys through approval and receipt recovery."""

from uuid import uuid4
from datetime import datetime, timezone

import httpx
import pytest

from atellagent_client.onboarding.device_csr import device_certificate_operation_id
from atellagent_client.onboarding.device_installation import DeviceInstallation
from atellagent_client.onboarding.device_setup_flow import DeviceSetupFlow
from atellagent_client.onboarding.errors import OnboardingClientError
from device_certificate_support import certificate_case


def fixture(tmp_path, monkeypatch):
    draft, receipt, trust, certificate = certificate_case()
    installation = DeviceInstallation(tmp_path / "installation")
    installation.setups.save_initial(draft)
    state = {"status": "pending", "issuance": None, "requests": [], "reporting": True}
    operation = device_certificate_operation_id(draft.operation_id)
    async def fetch_trust(origin):
        assert origin == draft.platform_origin
        return trust
    monkeypatch.setattr("atellagent_client.onboarding.device_setup_flow.fetch_device_trust", fetch_trust)
    factory = httpx.AsyncClient
    def handler(request):
        state["requests"].append(request)
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        if request.url.path == "/devices/self/status":
            return httpx.Response(200, json={"device_id": str(receipt.device_id), "tenant_id": str(draft.tenant_id),
                "owner_user_id": str(draft.owner_user_id), "device_name": "My Mac", "tenant_name": "Test organization", "status": "active",
                "discovery_reporting_status": "enabled" if state["reporting"] else "disabled",
                "certificate_expires_at": certificate["expires_at"], "discovery_observed_at": None,
                "discovery_fresh": False, "server_time": datetime.now(timezone.utc).isoformat()})
        if request.url.path.endswith("/activate"):
            return httpx.Response(200, json={"device_id": str(receipt.device_id),
                "status": "device_ready", "reporting_enabled": state["reporting"]})
        if request.url.path.endswith("/certificate"):
            if request.method == "POST":
                return httpx.Response(200, json={"operation_id": str(operation), "status": "pending",
                    "expires_at": receipt.expires_at.isoformat()})
            return httpx.Response(200, json=certificate)
        assert request.method == "GET"
        return httpx.Response(200, json={"id": str(draft.operation_id), "kind": "device",
            "status": state["status"], "device_id": str(state.get("device_id", receipt.device_id)),
            "expires_at": receipt.expires_at.isoformat(),
            "claim_expires_at": receipt.claim_expires_at.isoformat(), "device_issuance": state["issuance"]})
    monkeypatch.setattr("atellagent_client.onboarding.setup_status.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(handler)))
    flow = DeviceSetupFlow(installation=installation, draft=draft, receipt=receipt, reporting_enabled=True)
    return flow, state, operation


def issued(operation):
    return {"operation_id": str(operation), "status": "issued", "stage": "awaiting_activation",
        "reason_code": None, "attempt_count": 1, "next_attempt_at": None}


@pytest.mark.asyncio
async def test_approval_to_complete_installation_and_consumed_retry(tmp_path, monkeypatch):
    flow, state, operation = fixture(tmp_path, monkeypatch)
    assert (await flow.advance()).stage == "awaiting_approval"
    assert not flow.installation.path.exists()
    state["status"] = "approved"
    assert (await flow.advance()).stage == "issuing"
    assert not flow.installation.path.exists()
    state["issuance"] = issued(operation)
    ready = await flow.advance()
    assert ready.stage == "device_ready" and ready.activated.device.device_id == flow.receipt.device_id
    original = flow.installation.path.read_bytes()
    before_resume = len(state["requests"])
    state["status"], state["reporting"] = "consumed", False
    recovered = await flow.advance()
    assert recovered.activated.reporting_enabled is False
    assert flow.installation.path.read_bytes() == original
    assert len(list(flow.installation.credentials.directory.glob("device-*"))) == 1
    assert [request.url.path for request in state["requests"][before_resume:]] == [
        f"/api/client-onboarding/sessions/{flow.draft.operation_id}", "/devices/self/status"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["denied", "cancelled", "expired", "failed"])
async def test_terminal_setup_never_stages_or_selects(tmp_path, monkeypatch, status):
    flow, state, _ = fixture(tmp_path, monkeypatch)
    state["status"] = status
    with pytest.raises(OnboardingClientError, match="setup_" + status):
        await flow.advance()
    assert len(state["requests"]) == 1 and not flow.installation.path.exists()


@pytest.mark.asyncio
async def test_changed_assignment_cannot_stage_or_activate(tmp_path, monkeypatch):
    flow, state, _ = fixture(tmp_path, monkeypatch)
    state["device_id"] = uuid4()
    with pytest.raises(OnboardingClientError, match="identity_changed"):
        await flow.advance()
    assert len(state["requests"]) == 1


@pytest.mark.asyncio
async def test_wrong_issuance_identity_is_rejected_before_fetching_credentials(tmp_path, monkeypatch):
    flow, state, _ = fixture(tmp_path, monkeypatch)
    state["status"], state["issuance"] = "approved", issued(uuid4())
    with pytest.raises(OnboardingClientError, match="response_invalid"):
        await flow.advance()
    assert len(state["requests"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("stage", "device_ready"), ("attempt_count", True),
    ("reason_code", "arbitrary remote error"), ("extra", "secret")])
async def test_malformed_progress_does_not_trigger_issuance_or_activation(tmp_path, monkeypatch, field, value):
    flow, state, operation = fixture(tmp_path, monkeypatch)
    state["status"], state["issuance"] = "approved", issued(operation)
    state["issuance"][field] = value
    with pytest.raises(OnboardingClientError, match="response_invalid"):
        await flow.advance()
    assert len(state["requests"]) == 1 and not flow.installation.path.exists()


@pytest.mark.asyncio
async def test_selector_write_failure_after_activation_requires_recovery(tmp_path, monkeypatch):
    flow, state, operation = fixture(tmp_path, monkeypatch)
    state["status"], state["issuance"] = "approved", issued(operation)
    original = flow.installation.save
    def failed_save(**kwargs):
        raise OSError("simulated selector persistence failure")
    monkeypatch.setattr(flow.installation, "save", failed_save)
    with pytest.raises(OSError):
        await flow.advance()
    assert not flow.installation.path.exists()
    monkeypatch.setattr(flow.installation, "save", original)
    state["status"] = "consumed"
    assert (await flow.advance()).stage == "device_ready"
    assert len(list(flow.installation.credentials.directory.glob("device-*"))) == 1
