"""Interactive OAuth exchange remains bounded and independent of native hosts."""

import asyncio
import base64
from hashlib import sha256
from urllib.parse import parse_qs

import httpx
import pytest

from atellagent_client.onboarding.bootstrap_metadata import BootstrapMetadata
from atellagent_client.onboarding.device_login import DeviceLoginClient
from atellagent_client.onboarding.device_login_models import login_challenge
from atellagent_client.onboarding.errors import OnboardingClientError
from test_bootstrap_metadata import metadata


def contract():
    payload = metadata()
    return BootstrapMetadata.from_payload(payload, expected_origin=payload["platform_origin"])


def setup_http(monkeypatch, responses):
    requests, delays = [], []
    factory = httpx.AsyncClient
    async def respond(request):
        requests.append(request)
        status, payload = responses.pop(0)
        return httpx.Response(status, json=payload)
    def create(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return factory(**kwargs, transport=httpx.MockTransport(respond))
    async def sleep(delay):
        delays.append(delay)
    monkeypatch.setattr("atellagent_client.onboarding.device_login.httpx.AsyncClient", create)
    monkeypatch.setattr("atellagent_client.onboarding.device_login_poll.asyncio.sleep", sleep)
    return requests, delays


START = {"device_code": "private-device-code", "user_code": "ABCD-EFGH", "expires_in": 600,
    "interval": 5, "verification_uri_complete": "https://attacker.example/login"}
TOKENS = {"access_token": "private-access", "refresh_token": "private-refresh",
    "token_type": "Bearer", "expires_in": 300}


@pytest.mark.asyncio
async def test_pending_slowdown_and_success_use_trusted_urls_and_bound_verifier(monkeypatch):
    requests, delays = setup_http(monkeypatch, [(200, START),
        (400, {"error": "authorization_pending"}), (400, {"error": "slow_down"}), (200, TOKENS)])
    async with DeviceLoginClient(contract()) as client:
        challenge = await client.begin()
        assert challenge.browser_url == "https://atellagent.example/app/device?user_code=ABCD-EFGH"
        assert "private-device-code" not in repr(challenge)
        with pytest.raises(OnboardingClientError, match="login_in_progress"):
            await client.begin()
        result = await client.wait()
        assert result.access_token == "private-access" and "private-access" not in repr(result)
    assert delays == [5, 5, 10]
    start = parse_qs(requests[0].content.decode())
    token = parse_qs(requests[-1].content.decode())
    expected = base64.urlsafe_b64encode(sha256(token["code_verifier"][0].encode()).digest()).rstrip(b"=").decode()
    assert start["code_challenge"] == [expected]
    assert start["code_challenge_method"] == ["S256"]
    assert all("authorization" not in request.headers for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload,code,retryable", [
    (400, {"error": "access_denied"}, "onboarding_login_denied", False),
    (400, {"error": "expired_token"}, "onboarding_login_expired", False),
    (400, {"error": "secret-remote-body"}, "onboarding_login_rejected", False),
    (503, {"error": "secret-remote-body"}, "onboarding_oauth_unavailable", True),
    (302, {}, "onboarding_oauth_redirect_rejected", False),
    (200, {**TOKENS, "expires_in": True}, "onboarding_oauth_token_invalid", False),
])
async def test_failure_is_safe_and_clears_pending_state(monkeypatch, status, payload, code, retryable):
    setup_http(monkeypatch, [(200, START), (status, payload), (200, TOKENS)])
    async with DeviceLoginClient(contract()) as client:
        await client.begin()
        with pytest.raises(OnboardingClientError) as raised:
            await client.wait()
        assert raised.value.code == code and raised.value.retryable is retryable
        assert "secret-remote-body" not in str(raised.value)
        if retryable:
            assert (await client.wait()).access_token == TOKENS["access_token"]
        else:
            with pytest.raises(OnboardingClientError, match="login_not_started"):
                await client.wait()


@pytest.mark.parametrize("change", [{"expires_in": True}, {"interval": 0},
    {"expires_in": 901}, {"user_code": "bad\ncode"}, {"device_code": ""}])
def test_malformed_challenge_rejected(change):
    with pytest.raises(OnboardingClientError):
        login_challenge({**START, **change}, browser_url=contract().browser_authorization_url,
            verifier="private-verifier", started_at=1.0)


@pytest.mark.asyncio
async def test_close_cancels_polling_and_forbids_reuse(monkeypatch):
    setup_http(monkeypatch, [(200, START)])
    entered, hold = asyncio.Event(), asyncio.Event()
    async def sleeping(_):
        entered.set()
        await hold.wait()
    monkeypatch.setattr("atellagent_client.onboarding.device_login_poll.asyncio.sleep", sleeping)
    client = DeviceLoginClient(contract())
    await client.begin()
    waiter = asyncio.create_task(client.wait())
    await entered.wait()
    with pytest.raises(OnboardingClientError, match="login_in_progress"):
        await client.wait()
    await client.close()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    with pytest.raises(OnboardingClientError, match="login_closed"):
        await client.begin()


@pytest.mark.asyncio
async def test_expired_challenge_does_not_send_token_request(monkeypatch):
    requests, _ = setup_http(monkeypatch, [(200, START)])
    async with DeviceLoginClient(contract()) as client:
        challenge = await client.begin()
        monkeypatch.setattr("atellagent_client.onboarding.device_login_poll.monotonic",
            lambda: challenge.deadline + 1)
        with pytest.raises(OnboardingClientError, match="login_expired"):
            await client.wait()
    assert len(requests) == 1
