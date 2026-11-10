"""Bounded device transport errors and fresh signed attempts; no browser refresh."""

from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
import pytest

from atellagent_client.onboarding.device_requests import device_request
from atellagent_client.onboarding.errors import OnboardingClientError


@pytest.mark.asyncio
@pytest.mark.parametrize("method,delay,count", [("GET", "1", 2), ("GET", "1800", 1), ("POST", "1", 1)])
async def test_rate_limited_reads_retry_once_with_fresh_proof(monkeypatch, method, delay, count):
    calls = []
    sleeps = []
    async def sleep(seconds):
        sleeps.append(seconds)
    monkeypatch.setattr("atellagent_client.onboarding.device_requests.asyncio.sleep", sleep)
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"code": "onboarding_request_limited"}},
                                  headers={"Retry-After": delay})
        return httpx.Response(200, json={"success": True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        kwargs = dict(origin="https://device.example", device_id=uuid4(),
            proof_key=Ed25519PrivateKey.generate(), method=method, path="/devices/self/status")
        if count == 2:
            assert await device_request(client, **kwargs) == {"success": True}
            assert calls[0].headers != calls[1].headers
            assert sleeps == [1]
        else:
            with pytest.raises(OnboardingClientError) as error:
                await device_request(client, **kwargs)
            assert error.value.retry_after == int(delay)
            assert not sleeps
    assert len(calls) == count


@pytest.mark.asyncio
@pytest.mark.parametrize("status,content,code,retryable", [
    (302, b"", "device_redirect_rejected", False),
    (200, b"not json", "device_response_invalid", False),
    (200, b"x" * 65537, "device_response_invalid", False),
    (503, b"<html>private failure</html>", "device_request_rejected", True),
    (403, b'{"error":{"code":"onboarding_permission_denied","message":"secret"}}', "onboarding_permission_denied", False),
])
async def test_errors_are_bounded_safe_and_classified(status, content, code, retryable):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, content=content, headers={"Location": "https://other.example/steal"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(OnboardingClientError) as error:
            await device_request(client, origin="https://device.example", device_id=uuid4(),
                proof_key=Ed25519PrivateKey.generate(), method="GET", path="/devices/self/status")
    assert error.value.code == code and error.value.retryable is retryable
    assert "secret" not in str(error.value) and "private failure" not in str(error.value)
    assert len(calls) == 1 and "Authorization" not in calls[0].headers


@pytest.mark.asyncio
async def test_timeout_is_retryable_and_no_automatic_reauthentication_or_retry_occurs():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private details", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OnboardingClientError) as error:
            await device_request(client, origin="https://device.example", device_id=uuid4(),
                proof_key=Ed25519PrivateKey.generate(), method="GET", path="/devices/self/status")
    assert error.value.code == "device_transport_unavailable" and error.value.retryable
    assert len(calls) == 1 and "private details" not in str(error.value)
