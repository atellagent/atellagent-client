"""Exact persisted SA TLS context and setup proofs; no browser or device key."""

import base64
from hashlib import sha256
import json
import ssl

import httpx
import pytest

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.workload_activation import activate_stored_workload
from atellagent_client.onboarding.workload_activation_receipt import require_workload_activation_receipt
from test_workload_credential_store import stored_case
from test_workload_proof_client import case
from test_workload_management import draft


@pytest.fixture
def activation(stored_case, monkeypatch):
    store, args, credential = stored_case
    stored = store.save(**args, credential=credential)
    response = {"operation_id": str(credential.operation_id), "service_account_id": str(credential.service_account_id),
        "integration_id": str(credential.integration_id), "status": "credential_active", "runtime_status": "awaiting_registration"}
    calls, contexts = [], []
    def handler(request):
        assert request.url.host == "mtls.platform.example" and request.url.port == 8443
        assert request.url.path == f"/api/client-onboarding/requests/{args['draft'].proposal.id}/activate"
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        body = json.loads(request.content)
        assert body == {"operation_id": str(credential.operation_id), "certificate_sha256": credential.certificate_sha256}
        nonce = request.headers["X-Atellagent-Onboarding-Nonce"]
        material = "\n".join(("atellagent-onboarding-request-v1", str(args["draft"].setup.operation_id), "POST",
            request.url.path, sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            request.headers["X-Atellagent-Onboarding-Timestamp"], nonce)).encode()
        args["draft"].setup.keys.proof_key.public_key().verify(base64.urlsafe_b64decode(
            request.headers["X-Atellagent-Onboarding-Signature"] + "=="), material)
        calls.append(nonce)
        return httpx.Response(200, json=response)
    factory = httpx.AsyncClient
    def client(**kwargs):
        context = kwargs["verify"]
        assert isinstance(context, ssl.SSLContext) and context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED and context.minimum_version >= ssl.TLSVersion.TLSv1_2
        contexts.append(context)
        return factory(**kwargs, transport=httpx.MockTransport(handler))
    # The real SA factory still validates files and loads the cert/key into TLS.
    # Mock transport replaces network only; no actual server handshake is claimed.
    monkeypatch.setattr("atellagent_client.onboarding.service_account_http.httpx.AsyncClient", client)
    return args, store, stored, response, calls, contexts


@pytest.mark.asyncio
async def test_activation_receipt_recovery_reuses_committed_credentials(activation):
    args, store, stored, response, calls, contexts = activation
    with pytest.raises(OnboardingClientError):
        require_workload_activation_receipt(draft=args["draft"], receipt=args["receipt"], stored=stored)
    first = await activate_stored_workload(**args, credential_store=store)
    assert first.runtime_status == "awaiting_registration"
    require_workload_activation_receipt(draft=args["draft"], receipt=args["receipt"], stored=stored)
    history = (stored.certificate_path.parent / "activation.json").read_bytes()
    response["runtime_status"] = "registered"
    assert (await activate_stored_workload(**args, credential_store=store)).runtime_status == "registered"
    assert (stored.certificate_path.parent / "activation.json").read_bytes() == history
    assert len(calls) == len(set(calls)) == len(contexts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"status": "connected"}, {"runtime_status": "healthy"},
    {"service_account_id": "foreign"}, {"operation_id": "foreign"}, {"authorization_id": "native-only"}])
async def test_false_or_foreign_activation_never_creates_receipt(activation, change):
    args, store, stored, response, _, _ = activation
    response.update(change)
    with pytest.raises(OnboardingClientError):
        await activate_stored_workload(**args, credential_store=store)
    assert not (stored.certificate_path.parent / "activation.json").exists()


@pytest.mark.asyncio
async def test_lost_receipt_write_can_be_recovered_without_new_certificate(activation, monkeypatch):
    args, store, stored, _, calls, _ = activation
    from atellagent_client.onboarding import workload_activation_receipt as receipts
    create = receipts.create_private_file
    def unavailable(*args, **kwargs): raise OSError("storage denied")
    monkeypatch.setattr(receipts, "create_private_file", unavailable)
    with pytest.raises(OnboardingClientError, match="receipt_unavailable"):
        await activate_stored_workload(**args, credential_store=store)
    monkeypatch.setattr(receipts, "create_private_file", create)
    await activate_stored_workload(**args, credential_store=store)
    require_workload_activation_receipt(draft=args["draft"], receipt=args["receipt"], stored=stored)
    assert len(set(calls)) == 2


@pytest.mark.asyncio
async def test_mutated_activation_history_cannot_be_overwritten_as_success(activation):
    args, store, stored, _, _, _ = activation
    await activate_stored_workload(**args, credential_store=store)
    path = stored.certificate_path.parent / "activation.json"
    record = json.loads(path.read_bytes())
    record["revision"] = 1.0
    path.write_text(json.dumps(record))
    with pytest.raises(OnboardingClientError, match="receipt_unavailable"):
        await activate_stored_workload(**args, credential_store=store)
    assert json.loads(path.read_bytes())["revision"] == 1.0
