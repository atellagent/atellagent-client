# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Connection recovery must never replay activation or weaken request bounds."""

import asyncio
import ssl

import httpx
import pytest

from atellagent_client.onboarding import http_response as transport
from atellagent_client.onboarding.errors import OnboardingClientError

URL = "https://service.example/api/client-onboarding/requests/retained/runtime-metadata/original"
HEADERS = {"X-Atellagent-Onboarding-Nonce": "original-proof"}


async def perform(handler, *, method="GET", content=None):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await transport.request_json(client, method=method, url=URL,
            headers=HEADERS, content=content, error_prefix="onboarding")


@pytest.mark.asyncio
async def test_get_connect_recovery_preserves_exact_signed_request():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            raise httpx.ConnectError("All connection attempts failed", request=request)
        return httpx.Response(200, json={"ready": True})
    assert await perform(handler) == {"ready": True}
    assert len(calls) == 3
    assert all(str(r.url) == URL and r.method == "GET" and r.content == b""
        and r.headers["X-Atellagent-Onboarding-Nonce"] == "original-proof"
        and "authorization" not in r.headers and "cookie" not in r.headers for r in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,content,expected", [("GET", None, 3),
    ("GET", b"body", 1), ("POST", b"activation", 1), ("DELETE", None, 1)])
async def test_connect_attempts_are_finite_and_mutations_are_never_replayed(method, content, expected):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("private remote details", request=request)
    with pytest.raises(OnboardingClientError) as error:
        await perform(handler, method=method, content=content)
    assert len(calls) == expected
    assert error.value.code == "onboarding_transport_unavailable" and error.value.retryable
    assert "private remote details" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError])
async def test_uncertain_response_is_not_repeated(error_type):
    calls = []
    def handler(request):
        calls.append(request)
        raise error_type("response unavailable", request=request)
    with pytest.raises(OnboardingClientError):
        await perform(handler)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 409, 429, 500, 503])
async def test_http_rejections_are_not_retried(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"code": "scope_rejected"}},
            headers={"Retry-After": "2"})
    with pytest.raises(OnboardingClientError) as error:
        await perform(handler)
    assert len(calls) == 1 and error.value.status == status
    assert error.value.code == "scope_rejected" and error.value.retry_after == 2


@pytest.mark.asyncio
async def test_invalid_tls_is_not_retried():
    calls = []
    def handler(request):
        calls.append(request)
        try:
            raise ssl.SSLCertVerificationError("private certificate detail")
        except ssl.SSLCertVerificationError as exc:
            raise httpx.ConnectError("invalid TLS", request=request) from exc
    with pytest.raises(OnboardingClientError):
        await perform(handler)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_connection_exception_after_headers_is_not_retried():
    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.ConnectError("body failed after response started")
            yield b""  # Make this an asynchronous iterator.
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=BrokenBody())
    with pytest.raises(OnboardingClientError):
        await perform(handler)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_all_attempts_and_backoff_share_original_deadline(monkeypatch):
    original = asyncio.timeout
    deadlines, calls = [], []
    def short_timeout(seconds):
        deadlines.append(seconds)
        return original(0.01)
    monkeypatch.setattr(transport.asyncio, "timeout", short_timeout)
    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("connection refused", request=request)
    with pytest.raises(OnboardingClientError):
        await perform(handler)
    assert deadlines == [20] and len(calls) == 1


@pytest.mark.asyncio
async def test_cancellation_is_preserved():
    calls = []
    def handler(request):
        calls.append(request)
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await perform(handler)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response,code", [
    (httpx.Response(302, headers={"Location": "https://foreign.example"}), "redirect_rejected"),
    (httpx.Response(200, content=b"not JSON"), "response_invalid"),
    (httpx.Response(200, content=b"x" * 65537), "response_invalid"),
    (httpx.Response(200, json=[]), "response_invalid"),
])
async def test_redirect_and_response_bounds_still_fail_closed(response, code):
    calls = []
    def handler(request):
        calls.append(request)
        return response
    with pytest.raises(OnboardingClientError, match=code):
        await perform(handler)
    assert len(calls) == 1
