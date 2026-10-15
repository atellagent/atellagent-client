"""One supervisor keeps independently bound native controllers and device lifecycle."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from atellagent_client.runtime import supervisor as module
from atellagent_client.runtime import native_reconciliation as reconciliation
from atellagent_client.onboarding.errors import OnboardingClientError


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_two_host_startup_is_bound_independent_and_cleaned_up(monkeypatch, tmp_path, denied):
    hosts = ("codex", "claude-code")
    selected = {host: dict(host=host, request_id=uuid4()) for host in hosts}
    slots = SimpleNamespace(load=lambda host: selected[host], require_draft=Mock())
    monkeypatch.setattr(module, "NativeRuntimeSelections", lambda directory: slots)
    lifecycle = []
    @asynccontextmanager
    async def device():
        lifecycle.append("device-start")
        try:
            yield
        finally:
            lifecycle.append("device-stop")
    @asynccontextmanager
    async def context():
        yield SimpleNamespace(load=lambda request: SimpleNamespace(request_id=request))
    monkeypatch.setattr(module.DeviceRuntime, "open", AsyncMock(side_effect=lambda directory: device()))
    monkeypatch.setattr(reconciliation.NativeSetupContext, "open", AsyncMock(side_effect=lambda directory: context()))
    setups = {host: SimpleNamespace(config=SimpleNamespace(native_host=host), binding=object()) for host in hosts}
    async def opening(context, request):
        host = next(host for host in hosts if selected[host]["request_id"] == request)
        if denied and host == "codex":
            raise OnboardingClientError("native_delegation_revoked")
        return setups[host]
    monkeypatch.setattr(reconciliation, "open_native_runtime", opening)
    runtimes = []
    ready = asyncio.Event()
    async def started():
        if len(runtimes) == (1 if denied else 2): ready.set()
    def construct(config, **kwargs):
        assert kwargs["native_binding"] is setups[config.native_host].binding
        result = SimpleNamespace(start=AsyncMock(side_effect=started), stop=AsyncMock(), socket_path=kwargs["socket_path"])
        runtimes.append(result)
        return result
    await module.run(tmp_path, endpoint=lambda host: f"/private/{host}.sock",
                     wait_for_shutdown=ready.wait, runtime_factory=construct)
    assert lifecycle == ["device-start", "device-stop"]
    assert len(runtimes) == (1 if denied else 2)
    assert [item.socket_path for item in runtimes] == (["/private/claude-code.sock"] if denied else
        ["/private/codex.sock", "/private/claude-code.sock"])
    for runtime in runtimes:
        runtime.start.assert_awaited_once()
        runtime.stop.assert_awaited_once()
