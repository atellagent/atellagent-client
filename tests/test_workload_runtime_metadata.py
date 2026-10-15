"""Machine metadata requires local activation, real TLS files and exact proof."""

import base64
from hashlib import sha256
import ssl
import httpx
import pytest
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.workload_activation_receipt import save_workload_activation_receipt
from atellagent_client.onboarding.workload_runtime_metadata import fetch_workload_runtime_metadata
from test_workload_credential_store import stored_case
from test_workload_proof_client import case
from test_workload_management import draft


@pytest.fixture
def metadata(stored_case, monkeypatch):
    store, args, credential = stored_case
    stored = store.save(**args, credential=credential)
    save_workload_activation_receipt(draft=args["draft"], receipt=args["receipt"], stored=stored)
    payload = dict(platform_origin=args["draft"].setup.platform_origin,
        gateway_origin=args["trust"].service_account_api_origin,
        issuer_url="https://identity.example:9443/auth/realms/test",
        token_url="https://identity.example:9443/auth/realms/test/protocol/openid-connect/token",
        jwks_url="https://identity.example:9443/auth/realms/test/protocol/openid-connect/certs")
    calls = []
    def handler(request):
        assert str(request.url).startswith(args["trust"].service_account_api_origin + "/")
        assert request.method == "GET" and request.content == b""
        assert request.url.path == f"/api/client-onboarding/requests/{args['draft'].proposal.id}/runtime-metadata/{credential.operation_id}"
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        nonce = request.headers["X-Atellagent-Onboarding-Nonce"]
        material = "\n".join(("atellagent-onboarding-request-v1", str(args["draft"].setup.operation_id), "GET",
            request.url.path, sha256(b"").hexdigest(), request.headers["X-Atellagent-Onboarding-Timestamp"], nonce)).encode()
        args["draft"].setup.keys.proof_key.public_key().verify(base64.urlsafe_b64decode(
            request.headers["X-Atellagent-Onboarding-Signature"] + "=="), material)
        calls.append(nonce)
        return httpx.Response(200, json=payload)
    factory = httpx.AsyncClient
    def client(**kwargs):
        assert isinstance(kwargs["verify"], ssl.SSLContext) and kwargs["verify"].check_hostname
        assert kwargs["verify"].verify_mode == ssl.CERT_REQUIRED
        return factory(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr("atellagent_client.onboarding.service_account_http.httpx.AsyncClient", client)
    return args, store, stored, payload, calls


@pytest.mark.asyncio
async def test_exact_metadata_without_human_login(metadata):
    args, store, _, payload, calls = metadata
    for _ in range(2):
        result = await fetch_workload_runtime_metadata(**args, credential_store=store)
        assert result.token_url == payload["token_url"]
    assert len(set(calls)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("platform_origin", "https://foreign.example"),
    ("gateway_origin", "https://foreign.example:8443"), ("token_url", "https://foreign.example/token"),
    ("extra", "secret")])
async def test_substituted_or_extra_metadata_rejected(metadata, field, value):
    args, store, _, payload, _ = metadata
    payload[field] = value
    with pytest.raises(OnboardingClientError, match="metadata_invalid"):
        await fetch_workload_runtime_metadata(**args, credential_store=store)


@pytest.mark.asyncio
async def test_missing_activation_never_fetches_metadata(metadata):
    args, store, stored, _, calls = metadata
    (stored.certificate_path.parent / "activation.json").unlink()
    with pytest.raises(OnboardingClientError):
        await fetch_workload_runtime_metadata(**args, credential_store=store)
    assert calls == []
