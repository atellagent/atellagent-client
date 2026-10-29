"""One setup polls its signed exact request rather than enumerating history."""

import base64
from hashlib import sha256
from uuid import UUID

import httpx
import pytest

from atellagent_client.onboarding import device as module
from atellagent_client.onboarding.errors import OnboardingClientError
from test_device_assignments import assigned


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["found", "missing", "wrong", "duplicate"])
async def test_exact_lookup_has_fresh_signed_target_and_closed_result(assigned, monkeypatch, outcome):
    device, payload = assigned
    requested = UUID(int=2) if outcome == "wrong" else UUID(int=1)
    if outcome == "missing":
        payload["assignments"] = []
    elif outcome == "duplicate":
        payload["assignments"] *= 2
    nonces = []
    def handler(request):
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        target = f"/devices/self/assignments?request_id={requested}"
        assert str(request.url).endswith(target) and request.content == b""
        nonce = request.headers["X-Atellagent-Device-Nonce"]
        material = "\n".join(("atellagent-device-request-v1", str(device.device_id), "GET", target,
            sha256(b"").hexdigest(), request.headers["X-Atellagent-Device-Timestamp"], nonce)).encode()
        device.proof_key.public_key().verify(base64.urlsafe_b64decode(
            request.headers["X-Atellagent-Device-Signature"] + "=="), material)
        nonces.append(nonce)
        return httpx.Response(200, json=payload)
    monkeypatch.setattr(module, "create_device_http_client", lambda **kwargs:
        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    async with module.DeviceManagementClient(origin="https://device.example", device=device) as client:
        for _ in range(2):
            if outcome in ("wrong", "duplicate"):
                with pytest.raises(OnboardingClientError):
                    await client.assignment(requested)
            else:
                result = await client.assignment(requested)
                assert result is None if outcome == "missing" else result.request_id == requested
    assert len(set(nonces)) == 2


@pytest.mark.asyncio
async def test_untyped_target_is_not_coerced_or_sent(assigned, monkeypatch):
    device, _ = assigned
    monkeypatch.setattr(module, "create_device_http_client", lambda **kwargs:
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail("invalid target reached network"))))
    async with module.DeviceManagementClient(origin="https://device.example", device=device) as client:
        with pytest.raises(ValueError):
            await client.assignment(str(UUID(int=1)))
