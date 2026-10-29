"""Device lifecycle and supervisor accept zero configured controllers."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from contextlib import nullcontext

import httpx
import pytest

from atellagent_client.onboarding.device_runtime import DeviceRuntime


@pytest.mark.asyncio
async def test_runtime_shutdown_cancels_reporter_and_closes_connection():
    started, stopped, closed = asyncio.Event(), asyncio.Event(), []
    renewal_started, renewal_stopped = asyncio.Event(), asyncio.Event()
    async def report(stop):
        started.set()
        try:
            await stop.wait()
        finally:
            stopped.set()
    async def close():
        closed.append(True)
    async def renew(stop):
        renewal_started.set()
        try:
            await stop.wait()
        finally:
            renewal_stopped.set()
    runtime = DeviceRuntime(SimpleNamespace(aclose=close), SimpleNamespace(run=report), SimpleNamespace(run=renew), nullcontext())
    async with runtime:
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.wait_for(renewal_started.wait(), timeout=1)
    assert stopped.is_set() and renewal_stopped.is_set() and closed == [True]
    with pytest.raises(ValueError):
        async with runtime:
            pass


@pytest.mark.asyncio
async def test_shipping_supervisor_runs_device_lifecycle_without_controllers(monkeypatch, tmp_path):
    from atellagent_client.cli import supervisor
    calls = []
    class Runtime:
        async def __aenter__(self):
            calls.append("device-start")
        async def __aexit__(self, *_args):
            calls.append("device-stop")
    async def open_device(directory):
        assert directory == tmp_path
        return Runtime()
    async def shutdown():
        return None
    monkeypatch.setattr(supervisor, "installation_directory", lambda: tmp_path)
    from atellagent_client.runtime import supervisor as runtime_supervisor
    monkeypatch.setattr(runtime_supervisor.DeviceRuntime, "open", open_device)
    monkeypatch.setattr(supervisor, "wait_for_shutdown_signal", shutdown)
    await asyncio.wait_for(supervisor.run(), timeout=1)
    assert calls == ["device-start", "device-stop"]


@pytest.mark.asyncio
async def test_runtime_opens_persisted_credentials_without_human_auth(tmp_path, monkeypatch):
    from test_device_installation import prepared
    installation, args = prepared(tmp_path)
    installation.save(**args)
    async def trust(origin):
        assert origin == args["draft"].platform_origin
        return args["trust"]
    monkeypatch.setattr("atellagent_client.onboarding.device_runtime.fetch_device_trust", trust)
    requests = []
    factory = httpx.AsyncClient
    def respond(request):
        requests.append(request)
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        assert str(request.url) == args["trust"].device_api_origin + "/devices/self/status"
        return httpx.Response(200, json={"device_id": str(args["receipt"].device_id),
            "tenant_id": str(args["draft"].tenant_id), "owner_user_id": str(args["draft"].owner_user_id),
            "device_name": "Test device", "tenant_name": "Test organization", "status": "active", "discovery_reporting_status": "disabled",
            "certificate_expires_at": (datetime.now(timezone.utc).replace(year=2027)).isoformat(),
            "server_time": datetime.now(timezone.utc).isoformat(), "discovery_fresh": False,
            "discovery_observed_at": None})
    monkeypatch.setattr("atellagent_client.onboarding.device_http.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(respond)))
    runtime = await DeviceRuntime.open(installation.directory)
    from atellagent_client.onboarding.errors import OnboardingClientError
    with pytest.raises(OnboardingClientError, match="device_runtime_already_running"):
        await DeviceRuntime.open(installation.directory)
    assert not runtime.reporter.store.load().enabled
    async with runtime:
        await asyncio.sleep(0)
    assert len(requests) == 1 and runtime.client._http.is_closed
    # Release is an OS lease, not a lock-file sentinel. The existing file does
    # not prevent the next runtime after normal shutdown.
    restarted = await DeviceRuntime.open(installation.directory)
    async with restarted:
        await asyncio.sleep(0)
    assert len(requests) == 2 and restarted.client._http.is_closed


@pytest.mark.asyncio
async def test_failed_open_releases_runtime_lease(tmp_path):
    from atellagent_client.onboarding.process_lease import process_lease
    from atellagent_client.onboarding.errors import OnboardingClientError
    directory = tmp_path / "installation"
    with pytest.raises(OnboardingClientError):
        await DeviceRuntime.open(directory)
    with process_lease(directory.parent / ".device-runtime.lock") as held:
        assert held


@pytest.mark.asyncio
async def test_cancelled_start_releases_runtime_lease(tmp_path, monkeypatch):
    from test_device_installation import prepared
    from atellagent_client.onboarding.process_lease import process_lease
    installation, args = prepared(tmp_path)
    installation.save(**args)
    started = asyncio.Event()
    async def waiting_trust(_origin):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr("atellagent_client.onboarding.device_runtime.fetch_device_trust", waiting_trust)
    task = asyncio.create_task(DeviceRuntime.open(installation.directory))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with process_lease(installation.directory.parent / ".device-runtime.lock") as held:
        assert held
