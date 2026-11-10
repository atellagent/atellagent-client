"""Exact machine metadata on the signed device transport, not guessed URLs."""

import base64
from hashlib import sha256

import httpx
import pytest

from atellagent_client.onboarding import device as module
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.runtime_metadata import RuntimeMetadata
from test_device_assignments import assigned


def payload():
    issuer = "https://mtls.auth.example:9443/auth/realms/customer"
    return dict(platform_origin="https://platform.example", gateway_origin="https://mtls.gateway.example:8443",
                issuer_url=issuer, token_url=issuer + "/protocol/openid-connect/token",
                jwks_url=issuer + "/protocol/openid-connect/certs")


@pytest.mark.parametrize("field,value", [("platform_origin", "https://other.example"),
    ("gateway_origin", "https://other.example"), ("issuer_url", "http://auth.example/auth/realms/customer"),
    ("issuer_url", "https://auth.example/auth/realms/customer?other=1"),
    ("token_url", "https://other.example/token"), ("jwks_url", "https://other.example/certs"), ("extra", True)])
def test_changed_or_open_metadata_is_not_accepted(field, value):
    with pytest.raises(OnboardingClientError, match="onboarding_runtime_metadata_invalid"):
        RuntimeMetadata.from_payload({**payload(), field: value}, expected_origin="https://platform.example",
                                     expected_gateway_origin="https://mtls.gateway.example:8443")


@pytest.mark.asyncio
async def test_runtime_metadata_uses_fresh_signed_empty_get_without_human_token(assigned, monkeypatch):
    device, _ = assigned
    nonces = []
    def handler(request):
        target = "/devices/self/runtime-metadata"
        assert request.url.path == target and request.content == b""
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        nonce = request.headers["X-Atellagent-Device-Nonce"]
        material = "\n".join(("atellagent-device-request-v1", str(device.device_id), "GET", target,
            sha256(b"").hexdigest(), request.headers["X-Atellagent-Device-Timestamp"], nonce)).encode()
        device.proof_key.public_key().verify(base64.urlsafe_b64decode(
            request.headers["X-Atellagent-Device-Signature"] + "=="), material)
        nonces.append(nonce)
        return httpx.Response(200, json=payload())
    monkeypatch.setattr(module, "create_device_http_client", lambda **kwargs:
        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    async with module.DeviceManagementClient(origin="https://device.example", device=device) as client:
        for _ in range(2):
            result = await client.runtime_metadata(platform_origin="https://platform.example",
                                                   gateway_origin="https://mtls.gateway.example:8443")
            assert result.issuer_url == payload()["issuer_url"]
    assert len(set(nonces)) == 2
