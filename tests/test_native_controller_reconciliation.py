"""Exact local selection changes affect one runtime, not the shared reporter."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
import asyncio

import pytest

from atellagent_client.runtime import native_reconciliation as module
from atellagent_client.onboarding.errors import OnboardingClientError


@pytest.fixture
def case(monkeypatch, tmp_path):
    selected, setups, runtimes = {}, {}, []
    invalid = set()
    def load(host):
        if host in invalid:
            raise OnboardingClientError("onboarding_native_selection_invalid")
        return selected.get(host)
    slots = SimpleNamespace(load=load, require_draft=Mock())
    @asynccontextmanager
    async def context():
        yield SimpleNamespace(load=lambda request: SimpleNamespace(request_id=request))
    monkeypatch.setattr(module.NativeSetupContext, "open", AsyncMock(side_effect=lambda directory: context()))
    async def opening(context, request):
        return setups[request]
    monkeypatch.setattr(module, "open_native_runtime", opening)
    def construct(config, **kwargs):
        runtime = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), config=config,
            binding=kwargs["native_binding"], socket_path=kwargs["socket_path"])
        runtimes.append(runtime)
        return runtime
    def select(host):
        request = uuid4()
        selected[host] = dict(host=host, request_id=request)
        setups[request] = SimpleNamespace(config=SimpleNamespace(native_host=host), binding=object())
        return request
    manager = module.NativeControllerReconciler(tmp_path, slots, construct, lambda host: f"/private/{host}.sock")
    return SimpleNamespace(manager=manager, selected=selected, select=select,
        setups=setups, runtimes=runtimes, invalid=invalid)


@pytest.mark.asyncio
async def test_add_remove_replace_and_shutdown_touch_only_the_changed_host(case):
    await case.manager.reconcile()
    assert not case.runtimes
    case.select("codex"); case.select("claude-code")
    await case.manager.reconcile()
    codex, claude = case.runtimes
    del case.selected["codex"]
    await case.manager.reconcile()
    codex.stop.assert_awaited_once()
    claude.stop.assert_not_awaited()
    case.select("codex")
    await case.manager.reconcile()
    replacement = case.runtimes[-1]
    assert replacement is not codex
    await case.manager.reconcile()
    replacement.start.assert_awaited_once()
    claude.start.assert_awaited_once()
    await case.manager.close()
    replacement.stop.assert_awaited_once()
    claude.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_corrupt_selection_stops_its_runtime_without_hiding_peer(case):
    case.select("codex"); case.select("claude-code")
    await case.manager.reconcile()
    codex, claude = case.runtimes
    case.invalid.add("codex")
    await case.manager.reconcile()
    codex.stop.assert_awaited_once()
    claude.stop.assert_not_awaited()
    assert "codex" not in case.manager.active and "claude-code" in case.manager.active
    await case.manager.close()


@pytest.mark.asyncio
async def test_selection_removed_during_verification_never_starts(case, monkeypatch):
    request = case.select("codex")
    async def opening(context, value):
        assert value == request
        del case.selected["codex"]
        return case.setups[value]
    monkeypatch.setattr(module, "open_native_runtime", opening)
    await case.manager.reconcile()
    assert not case.runtimes and not case.manager.active


@pytest.mark.asyncio
async def test_failed_stop_prevents_replacement_start(case):
    case.select("codex")
    await case.manager.reconcile()
    original = case.runtimes[0]
    original.stop.side_effect = OSError("cannot stop owned controller")
    case.select("codex")
    with pytest.raises(OSError):
        await case.manager.reconcile()
    assert len(case.runtimes) == 1
    original.stop.side_effect = None
    await case.manager.close()


@pytest.mark.asyncio
async def test_retryable_startup_is_backed_off_but_a_new_request_can_start(case, monkeypatch):
    original = case.select("codex")
    now = [0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    calls = []
    async def opening(context, request):
        calls.append(request)
        if request == original:
            raise OnboardingClientError("onboarding_transport_unavailable", retryable=True)
        return case.setups[request]
    monkeypatch.setattr(module, "open_native_runtime", opening)
    await case.manager.reconcile()
    await case.manager.reconcile()
    assert calls == [original]
    now[0] = 31
    await case.manager.reconcile()
    assert calls == [original, original]
    replacement = case.select("codex")
    await case.manager.reconcile()
    assert calls[-1] == replacement and len(case.runtimes) == 1
    await case.manager.close()


@pytest.mark.asyncio
async def test_slow_host_verification_does_not_delay_peer_start(case, monkeypatch):
    codex = case.select("codex")
    case.select("claude-code")
    entered, release, peer_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    construct = case.manager.runtime_factory
    def runtime(config, **kwargs):
        result = construct(config, **kwargs)
        if config.native_host == "claude-code":
            result.start.side_effect = peer_started.set
        return result
    case.manager.runtime_factory = runtime
    async def opening(context, request):
        if request == codex:
            entered.set()
            await release.wait()
        return case.setups[request]
    monkeypatch.setattr(module, "open_native_runtime", opening)
    task = asyncio.create_task(case.manager.reconcile())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(peer_started.wait(), 1)
        assert not task.done() and "claude-code" in case.manager.active
        release.set()
        await task
        await case.manager.reconcile()
        assert len(case.runtimes) == 2
        for controller in case.runtimes:
            controller.start.assert_awaited_once()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await case.manager.close()


@pytest.mark.asyncio
async def test_shutdown_starts_both_cleanups_before_waiting_for_either(case):
    case.select("codex"); case.select("claude-code")
    await case.manager.reconcile()
    entered = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    for index, runtime in enumerate(case.runtimes):
        async def stop(index=index):
            entered[index].set()
            await release.wait()
        runtime.stop.side_effect = stop
    task = asyncio.create_task(case.manager.close())
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 1)
        assert not task.done() and len(case.manager.active) == 2
    finally:
        release.set()
        await task
    assert not case.manager.active
