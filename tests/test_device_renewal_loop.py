"""Device renewal runs even without agents, reporting or a human session."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from unittest.mock import Mock

from atellagent_client.onboarding.device_renewal_loop import DeviceRenewalLoop
from atellagent_client.onboarding.errors import OnboardingClientError


@pytest.mark.asyncio
async def test_loop_uses_current_generation_and_does_not_renew_early(monkeypatch):
    calls = []
    client = SimpleNamespace(origin="https://device.example", device="parent")
    trust = SimpleNamespace(device_api_origin=client.origin)
    def metadata(*, device, trust):
        calls.append(device)
        now = datetime.now(timezone.utc)
        return SimpleNamespace(not_before=now - timedelta(days=89 if device == "parent" else 0),
            expires_at=now + timedelta(days=1 if device == "parent" else 90), certificate_sha256="a" * 64)
    async def renew(*, trust: object):
        assert trust is loop.trust
        client.device = "replacement"
    client.renew_certificate = renew
    monkeypatch.setattr("atellagent_client.onboarding.device_renewal_loop.retained_device_certificate_metadata",
        metadata)
    loop = DeviceRenewalLoop(client, trust, Mock())
    assert await loop.tick()
    assert not await loop.tick()
    assert calls == ["parent", "replacement", "replacement"]


@pytest.mark.asyncio
async def test_run_surfaces_authority_loss_and_stops_without_login(monkeypatch, caplog):
    stop = asyncio.Event()
    client = SimpleNamespace(origin="https://device.example", device="expired")
    trust = SimpleNamespace(device_api_origin=client.origin)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr("atellagent_client.onboarding.device_renewal_loop.retained_device_certificate_metadata",
        lambda **kwargs: SimpleNamespace(not_before=now - timedelta(days=90), expires_at=now - timedelta(seconds=1),
            certificate_sha256="a" * 64))
    async def renew(*, trust):
        stop.set()
        raise OnboardingClientError("device_authority_revoked")
    client.renew_certificate = renew
    await asyncio.wait_for(DeviceRenewalLoop(client, trust, Mock()).run(stop), timeout=1)
    assert "device_renewal_failed code=device_authority_revoked" in caplog.text


@pytest.mark.asyncio
async def test_retry_warning_is_nonterminal_and_success_clears_it(monkeypatch):
    stop = asyncio.Event()
    client = SimpleNamespace(origin="https://device.example", device="current")
    state = Mock()
    loop = DeviceRenewalLoop(client, SimpleNamespace(device_api_origin=client.origin), state)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr("atellagent_client.onboarding.device_renewal_loop.retained_device_certificate_metadata",
        lambda **kwargs: SimpleNamespace(not_before=now - timedelta(days=80), expires_at=now + timedelta(days=10),
            certificate_sha256="a" * 64))
    async def unavailable(**kwargs):
        stop.set()
        raise OnboardingClientError("device_transport_unavailable", retryable=True)
    client.renew_certificate = unavailable
    await loop.run(stop)
    state.save.assert_called_once_with("device_renewal_retrying", certificate_sha256="a" * 64)
    async def success(**kwargs):
        pass
    client.renew_certificate = success
    assert await loop.tick()
    assert state.save.call_args.args == (None,)


@pytest.mark.asyncio
async def test_terminal_operation_waits_for_shutdown_without_repeated_requests(monkeypatch, caplog):
    stop, observed = asyncio.Event(), asyncio.Event()
    client = SimpleNamespace(origin="https://device.example", device="parent")
    state = Mock()
    loop = DeviceRenewalLoop(client, SimpleNamespace(device_api_origin=client.origin), state)
    calls = []
    event_loop = asyncio.get_running_loop()
    state.save.side_effect = lambda *args, **kwargs: event_loop.call_soon_threadsafe(observed.set)
    async def terminal():
        calls.append(True)
        raise OnboardingClientError("device_renewal_recovery_required")
    monkeypatch.setattr(loop, "tick", terminal)
    monkeypatch.setattr("atellagent_client.onboarding.device_renewal_loop.retained_device_certificate_metadata",
        lambda **kwargs: SimpleNamespace(certificate_sha256="a" * 64))
    task = asyncio.create_task(loop.run(stop))
    try:
        await asyncio.wait_for(observed.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not task.done() and calls == [True]
        assert "action=approved_device_recovery" in caplog.text
        stop.set()
        await asyncio.wait_for(task, timeout=1)
        state.save.assert_called_once_with("device_renewal_recovery_required", certificate_sha256="a" * 64)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
