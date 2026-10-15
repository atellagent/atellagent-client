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
    def expiry(*, device, trust):
        calls.append(device)
        return datetime.now(timezone.utc) + timedelta(days=1 if device == "parent" else 30)
    async def renew(*, trust: object):
        assert trust is loop.trust
        client.device = "replacement"
    client.renew_certificate = renew
    monkeypatch.setattr("atellagent_client.onboarding.device_renewal_loop.retained_device_certificate_expiry", expiry)
    monkeypatch.setattr("atellagent_client.onboarding.device_renewal_loop.retained_device_certificate_metadata",
        lambda **kwargs: SimpleNamespace(certificate_sha256="a" * 64))
    loop = DeviceRenewalLoop(client, trust, Mock())
    assert await loop.tick()
    assert not await loop.tick()
    assert calls == ["parent", "replacement"]


@pytest.mark.asyncio
async def test_run_surfaces_authority_loss_and_stops_without_login(monkeypatch, caplog):
    stop = asyncio.Event()
    client = SimpleNamespace(origin="https://device.example", device="expired")
    trust = SimpleNamespace(device_api_origin=client.origin)
    monkeypatch.setattr("atellagent_client.onboarding.device_renewal_loop.retained_device_certificate_expiry",
        lambda **kwargs: datetime.now(timezone.utc) - timedelta(seconds=1))
    async def renew(*, trust):
        stop.set()
        raise OnboardingClientError("device_authority_revoked")
    client.renew_certificate = renew
    await asyncio.wait_for(DeviceRenewalLoop(client, trust, Mock()).run(stop), timeout=1)
    assert "device_renewal_failed code=device_authority_revoked" in caplog.text


@pytest.mark.asyncio
async def test_terminal_operation_waits_for_shutdown_without_repeated_requests(monkeypatch, caplog):
    stop, observed = asyncio.Event(), asyncio.Event()
    client = SimpleNamespace(origin="https://device.example", device="parent")
    state = Mock()
    loop = DeviceRenewalLoop(client, SimpleNamespace(device_api_origin=client.origin), state)
    calls = []
    async def terminal():
        calls.append(True)
        observed.set()
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
