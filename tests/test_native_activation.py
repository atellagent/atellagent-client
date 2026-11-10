"""SA mTLS plus device proof activates credentials, not host readiness."""

import base64
from hashlib import sha256
import json
import ssl
from types import SimpleNamespace
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import serialization
import httpx
import pytest

from atellagent_client.onboarding.native_activation import activate_stored_native
from atellagent_client.onboarding.native_activation_receipt import NativeActivationResult
from atellagent_client.onboarding.native_credential_store import NativeCredentialStore
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.setup_keys import SetupKeys
from native_credential_support import stored_case


@pytest.fixture
def activation(tmp_path):
    case = stored_case(tmp_path)
    store = NativeCredentialStore(tmp_path / "credentials")
    stored = store.save(**{field: getattr(case, field) for field in ("draft", "assignment", "trust", "credential", "draft_store")})
    store.select(**{field: getattr(case, field) for field in ("draft", "assignment", "trust", "draft_store")},
                 fingerprint=case.credential.certificate_sha256)
    draft = case.draft
    setup = SimpleNamespace(kind="device", operation_id=draft.setup_id, platform_origin=draft.platform_origin,
        tenant_id=draft.tenant_id, owner_user_id=draft.owner_user_id, keys=SetupKeys.generate())
    receipt = SimpleNamespace(kind="device", id=draft.setup_id, device_id=draft.device_id)
    kwargs = dict(draft=draft, assignment=case.assignment, trust=case.trust, draft_store=case.draft_store,
        credential_store=store, fingerprint=case.credential.certificate_sha256,
        installation=SimpleNamespace(load=lambda: (setup, receipt, "a" * 64)))
    response = dict(operation_id=str(case.credential.operation_id), service_account_id=str(case.credential.service_account_id),
        integration_id=str(case.credential.integration_id), authorization_id=str(uuid4()), authorization_epoch=1,
        status="credential_active", runtime_status="awaiting_registration")
    return SimpleNamespace(case=case, setup=setup, stored=stored, kwargs=kwargs, response=response)


@pytest.mark.asyncio
async def test_activation_uses_separate_ssl_pool_and_original_device_proof(activation, monkeypatch):
    item, nonces = activation, []
    def handler(request):
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        body = json.loads(request.content)
        assert body == {"operation_id": item.response["operation_id"], "certificate_sha256": item.kwargs["fingerprint"]}
        nonce = request.headers["X-Atellagent-Onboarding-Nonce"]
        material = "\n".join(("atellagent-onboarding-request-v1", str(item.case.draft.setup_id), "POST", request.url.path,
            sha256(request.content).hexdigest(), request.headers["X-Atellagent-Onboarding-Timestamp"], nonce)).encode()
        item.setup.keys.proof_key.public_key().verify(base64.urlsafe_b64decode(
            request.headers["X-Atellagent-Onboarding-Signature"] + "=="), material)
        nonces.append(nonce)
        return httpx.Response(200, json=item.response)
    factory = httpx.AsyncClient
    def create(**kwargs):
        context = kwargs["verify"]
        assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        root = x509.load_pem_x509_certificate(item.case.trust.ca_bundle_pem.encode()).public_bytes(serialization.Encoding.DER)
        assert root not in context.get_ca_certs(binary_form=True)
        return factory(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr("atellagent_client.onboarding.service_account_http.httpx.AsyncClient", create)
    first = await activate_stored_native(**item.kwargs)
    assert first.runtime_status == "awaiting_registration"
    receipt = (item.stored.certificate_path.parent / "activation.json").read_bytes()
    item.response["runtime_status"] = "registered"
    assert (await activate_stored_native(**item.kwargs)).runtime_status == "registered"
    assert (item.stored.certificate_path.parent / "activation.json").read_bytes() == receipt
    assert len(set(nonces)) == 2


@pytest.mark.parametrize("change", [{"operation_id": str(uuid4())}, {"service_account_id": str(uuid4())},
    {"integration_id": str(uuid4())}, {"authorization_id": "bad"}, {"authorization_epoch": True},
    {"authorization_epoch": 0}, {"authorization_epoch": 2**31}, {"status": "connected"},
    {"runtime_status": "healthy"}, {"unexpected": True}])
def test_activation_ack_does_not_adopt_wrong_identity_or_claim_readiness(activation, change):
    with pytest.raises(OnboardingClientError):
        NativeActivationResult.from_payload({**activation.response, **change}, stored=activation.stored)
    assert not (activation.stored.certificate_path.parent / "activation.json").exists()


@pytest.mark.asyncio
async def test_wrong_selected_device_setup_never_opens_connection(activation, monkeypatch):
    activation.setup.operation_id = uuid4()
    monkeypatch.setattr("atellagent_client.onboarding.native_activation.create_service_account_http_client",
                        lambda **kwargs: pytest.fail("wrong device reached network"))
    with pytest.raises(OnboardingClientError):
        await activate_stored_native(**activation.kwargs)


@pytest.mark.asyncio
async def test_activation_requires_durable_selection_before_network(activation, monkeypatch):
    pointer = activation.kwargs["credential_store"].directory / f"{activation.case.draft.proposal.id}-selected.json"
    pointer.unlink()
    monkeypatch.setattr("atellagent_client.onboarding.native_activation.create_service_account_http_client",
                        lambda **kwargs: pytest.fail("unselected credential reached network"))
    with pytest.raises(OnboardingClientError, match="onboarding_native_credential_recovery_required"):
        await activate_stored_native(**activation.kwargs)
