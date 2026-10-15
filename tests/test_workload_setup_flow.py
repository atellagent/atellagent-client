"""Resumable workload steps use actual retained keys/storage and mocked HTTP."""

import json
from types import SimpleNamespace

import httpx
import pytest

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.workload_activation_receipt import require_workload_activation_receipt
from atellagent_client.onboarding.workload_receipt_store import WorkloadReceiptStore
from atellagent_client.onboarding.workload_setup_flow import WorkloadSetupFlow
from test_workload_credential_store import stored_case
from test_workload_proof_client import case
from test_workload_management import draft


@pytest.fixture
def flow_case(stored_case, monkeypatch):
    credentials, args, credential = stored_case
    receipts = WorkloadReceiptStore(args["draft_store"])
    state = SimpleNamespace(status="pending", calls=[], lost_activation=False, unknown=False)
    trust = args["trust"]
    trust_payload = {"platform_origin": trust.platform_origin, "service_account_api_origin": trust.service_account_api_origin,
        "ca_bundle_pem": trust.ca_bundle_pem, "ca_bundle_sha256": trust.ca_bundle_sha256,
        "protocol_revision": 1, "certificate_profile": "atellagent-service-account-client-v1"}
    from atellagent_client.onboarding.service_account_delivery_payload import service_account_delivery_payload
    def handler(request):
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        state.calls.append(request.url.path)
        if request.url.path.startswith("/.well-known/"):
            assert request.url.host == "platform.example"
            return httpx.Response(200, json=trust_payload)
        assert "X-Atellagent-Onboarding-Signature" in request.headers or request.method == "POST"
        if "/runtime-metadata/" in request.url.path:
            assert state.status in {"installed", "connected"}
            assert request.url.host == "mtls.platform.example"
            return httpx.Response(200, json=dict(platform_origin=trust.platform_origin,
                gateway_origin=trust.service_account_api_origin,
                issuer_url="https://identity.example:9443/auth/realms/test",
                token_url="https://identity.example:9443/auth/realms/test/protocol/openid-connect/token",
                jwks_url="https://identity.example:9443/auth/realms/test/protocol/openid-connect/certs"))
        if request.url.path.endswith("/status"):
            if state.unknown:
                return httpx.Response(403, json={"error": {"code": "onboarding_permission_denied"}})
            return httpx.Response(200, json={**args["receipt"].payload(), "status": state.status})
        if request.url.path.endswith("/claim"):
            assert state.status == "approved"
            payload = json.loads(request.content)
            assert "proof" in payload and "PRIVATE KEY" not in str(payload)
            state.status = "issuing"
            return httpx.Response(200, json={"id": str(args["draft"].proposal.id), "status": "issuing", "revision": 1,
                "expires_at": args["receipt"].expires_at.isoformat(), "claim_expires_at": "2026-10-08T11:00:00Z"})
        if request.url.path.endswith("/certificate"):
            return httpx.Response(200, json={"status": "issuing"} if state.status == "issuing"
                                 else service_account_delivery_payload(credential))
        assert request.url.path.endswith("/activate") and request.url.host == "mtls.platform.example"
        state.status = "installed"
        if state.lost_activation:
            state.lost_activation = False
            raise httpx.ReadError("response lost", request=request)
        return httpx.Response(200, json={"operation_id": str(credential.operation_id),
            "service_account_id": str(credential.service_account_id), "integration_id": str(credential.integration_id),
            "status": "credential_active", "runtime_status": "awaiting_registration"})
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.workload_proof_client.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(handler)))
    flow = WorkloadSetupFlow(draft=args["draft"], draft_store=args["draft_store"], receipt_store=receipts,
                             credential_store=credentials)
    return SimpleNamespace(flow=flow, state=state, args=args, receipts=receipts,
                           credentials=credentials, credential=credential)


@pytest.mark.asyncio
async def test_lost_submission_receipt_then_approval_issuance_activation(flow_case):
    c = flow_case
    assert (await c.flow.advance()).stage == "awaiting_approval"
    assert c.receipts.load(c.args["draft"]).service_account_id == c.credential.service_account_id
    assert not list(c.credentials.directory.iterdir())
    c.state.status = "approved"
    assert (await c.flow.advance()).stage == "issuing"
    assert (await c.flow.advance()).stage == "issuing"
    c.state.status = "issued"
    ready = await c.flow.advance()
    assert ready.stage == "awaiting_registration" and ready.activation is not None
    stored = c.credentials.load(**c.args)
    require_workload_activation_receipt(draft=c.args["draft"], receipt=c.args["receipt"], stored=stored)
    assert not any(path.endswith("/sessions") or path.endswith("/workload") for path in c.state.calls)


@pytest.mark.asyncio
async def test_lost_activation_response_recovers_without_reissuing(flow_case):
    c = flow_case
    c.receipts.bind(draft=c.args["draft"], receipt=c.args["receipt"])
    c.state.status, c.state.lost_activation = "issued", True
    with pytest.raises(OnboardingClientError, match="transport_unavailable"):
        await c.flow.advance()
    first = c.credentials.load(**c.args)
    c.state.calls.clear()
    assert (await c.flow.advance()).stage == "awaiting_registration"
    assert c.credentials.load(**c.args) == first
    assert not any(path.endswith("/claim") or path.endswith("/certificate") for path in c.state.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["denied", "cancelled", "expired", "failed"])
async def test_terminal_requests_do_not_provision_or_fetch_trust(flow_case, status):
    c = flow_case
    c.state.status = status
    with pytest.raises(OnboardingClientError, match="setup_" + status):
        await c.flow.advance()
    assert all(path.endswith("/status") for path in c.state.calls)


@pytest.mark.asyncio
async def test_unknown_submission_does_not_auto_create_a_new_request(flow_case):
    c = flow_case
    c.state.unknown = True
    with pytest.raises(OnboardingClientError, match="permission_denied"):
        await c.flow.advance()
    assert len(c.state.calls) == 1 and c.state.calls[0].endswith("/status")


@pytest.mark.asyncio
async def test_installed_workload_without_local_material_cannot_reenroll(flow_case):
    c = flow_case
    c.state.status = "installed"
    with pytest.raises(OnboardingClientError, match="credential_recovery_required"):
        await c.flow.advance()
    assert not any(path.endswith("/claim") or path.endswith("/certificate") or path.endswith("/activate")
                   for path in c.state.calls)
