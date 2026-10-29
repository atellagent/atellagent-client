"""Supervisor shutdown owns and finishes interrupted reconciliation before exit."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from atellagent_client.runtime import supervisor as module
from atellagent_client.onboarding.process_lease import process_lease
from test_native_controller_reconciliation import case


def owned_case(tmp_path, monkeypatch, *, stalled=False, failure=None):
    events = []
    entered, shutdown = asyncio.Event(), asyncio.Event()
    @asynccontextmanager
    async def device():
        events.append('device-enter')
        try: yield
        finally: events.append('device-exit')
    async def reconcile():
        events.append('reconcile-enter'); entered.set()
        try:
            if failure: raise RuntimeError('owned reconciliation failure')
            if stalled: await asyncio.Future()
        finally: events.append('reconcile-finished')
    async def close_controller():
        assert 'reconcile-enter' not in events or 'reconcile-finished' in events
        events.append('controllers-close')
    async def reconcile_mcp():
        events.append('mcp-reconcile');shutdown.set()
    async def close_mcp(): events.append('mcp-close')
    controllers = SimpleNamespace(reconcile=reconcile, close=close_controller)
    mcp = SimpleNamespace(reconcile=reconcile_mcp, close=close_mcp)
    monkeypatch.setattr(module.DeviceRuntime, 'open', AsyncMock(side_effect=lambda _:device()))
    monkeypatch.setattr(module, 'NativeRuntimeSelections', lambda _:None)
    monkeypatch.setattr(module, 'NativeControllerReconciler', lambda *_:controllers)
    monkeypatch.setattr(module, 'MCPRuntimeReconciler', lambda _:mcp)
    async def run():
        await module.run(tmp_path, endpoint=lambda _: 'owned-unused', wait_for_shutdown=shutdown.wait)
    return SimpleNamespace(events=events, entered=entered, shutdown=shutdown, run=run)


def assert_paired_cleanup(events):
    assert events[0] == 'device-enter' and events[-1] == 'device-exit'
    for event in ['device-enter', 'reconcile-enter', 'reconcile-finished',
                  'mcp-reconcile', 'mcp-close', 'controllers-close', 'device-exit']:
        assert events.count(event) == 1
    assert len(events) == 7
    assert events.index('reconcile-enter') < events.index('reconcile-finished')
    for event in ['mcp-close', 'controllers-close']:
        assert events.index('reconcile-finished') < events.index(event) < events.index('device-exit')


@pytest.mark.asyncio
async def test_signal_during_startup_cancels_reconciliation_before_device_exit(tmp_path, monkeypatch):
    case = owned_case(tmp_path, monkeypatch, stalled=True)
    task = asyncio.create_task(case.run())
    try:
        await asyncio.wait_for(case.entered.wait(), 1)
        case.shutdown.set()
        done, _ = await asyncio.wait({task}, timeout=.2)
        assert task in done, 'Shutdown waited on stalled controller verification/startup'
        await task
        assert_paired_cleanup(case.events)
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_external_cancellation_finishes_owned_reconciliation_before_exit(tmp_path, monkeypatch):
    case = owned_case(tmp_path, monkeypatch, stalled=True)
    task = asyncio.create_task(case.run())
    await asyncio.wait_for(case.entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 1)
    assert case.events[-4:] == ['reconcile-finished', 'mcp-close', 'controllers-close', 'device-exit']


@pytest.mark.asyncio
async def test_normal_selection_reconcile_and_cleanup_order_remain(tmp_path, monkeypatch):
    case = owned_case(tmp_path, monkeypatch)
    await asyncio.wait_for(case.run(), 1)
    assert case.events == ['device-enter', 'reconcile-enter', 'reconcile-finished', 'mcp-reconcile', 'mcp-close', 'controllers-close', 'device-exit']


@pytest.mark.asyncio
async def test_shutdown_already_requested_does_not_start_new_runtimes(tmp_path, monkeypatch):
    case = owned_case(tmp_path, monkeypatch)
    case.shutdown.set()
    await asyncio.wait_for(case.run(), 1)
    assert case.events == ['device-enter', 'mcp-close', 'controllers-close', 'device-exit']


@pytest.mark.asyncio
async def test_reconciliation_failure_propagates_after_paired_cleanup(tmp_path, monkeypatch):
    case = owned_case(tmp_path, monkeypatch, failure=True)
    with pytest.raises(RuntimeError, match='owned reconciliation failure'):
        await asyncio.wait_for(case.run(), 1)
    assert_paired_cleanup(case.events)


@pytest.mark.asyncio
async def test_shutdown_waiter_failure_propagates_and_cancels_startup(tmp_path, monkeypatch):
    case = owned_case(tmp_path, monkeypatch, stalled=True)
    async def failed_shutdown():
        await case.entered.wait()
        raise RuntimeError('owned shutdown failure')
    task = asyncio.create_task(module.run(tmp_path, endpoint=lambda _: 'unused', wait_for_shutdown=failed_shutdown))
    try:
        done, _ = await asyncio.wait({task}, timeout=.2)
        assert task in done, 'Failed shutdown waiter did not stop the owned startup task'
        with pytest.raises(RuntimeError, match='owned shutdown failure'): await task
        assert case.events[-4:] == ['reconcile-finished', 'mcp-close', 'controllers-close', 'device-exit']
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_actual_native_reconciler_stops_partial_start_before_releasing_device_lease(case, tmp_path, monkeypatch):
    case.select('codex')
    entered, shutdown = asyncio.Event(), asyncio.Event()
    parent = tmp_path / 'private-owner';parent.mkdir(mode=0o700)
    lock = parent / '.device-runtime.lock'
    construct = case.manager.runtime_factory
    async def starting(): entered.set();await asyncio.Future()
    def runtime(*args, **kwargs):
        value = construct(*args, **kwargs)
        value.start = AsyncMock(side_effect=starting)
        return value
    case.manager.runtime_factory = runtime
    @asynccontextmanager
    async def device():
        with process_lease(lock) as held:
            assert held
            try: yield
            finally:
                assert not case.manager.active, 'Device lease released before partial native runtime cleanup'
    monkeypatch.setattr(module.DeviceRuntime, 'open', AsyncMock(side_effect=lambda _:device()))
    monkeypatch.setattr(module, 'NativeRuntimeSelections', lambda _:case.manager.selections)
    monkeypatch.setattr(module, 'NativeControllerReconciler', lambda *_:case.manager)
    monkeypatch.setattr(module, 'MCPRuntimeReconciler', lambda _:SimpleNamespace(reconcile=AsyncMock(), close=AsyncMock()))
    task = asyncio.create_task(module.run(tmp_path, endpoint=lambda _: 'unused', wait_for_shutdown=shutdown.wait))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with process_lease(lock) as held: assert not held
        shutdown.set()
        done, _ = await asyncio.wait({task}, timeout=.2)
        assert task in done, 'Actual native startup prevented shutdown ownership handoff'
        await task
        assert len(case.runtimes) == 1
        case.runtimes[0].stop.assert_awaited_once()
        with process_lease(lock) as held: assert held
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_reconciliation_error_racing_shutdown_is_not_hidden(tmp_path, monkeypatch):
    case = owned_case(tmp_path, monkeypatch)
    async def failure():
        case.events.append('reconcile-enter')
        case.shutdown.set()
        case.events.append('reconcile-finished')
        raise RuntimeError('owned failure concurrent with shutdown')
    monkeypatch.setattr(module, '_reconcile', failure, raising=False)
    # Use the actual supervisor with a reconciliation callback that finishes
    # exceptionally in the same scheduling window as normal shutdown.
    async def one_cycle(*_): await failure()
    monkeypatch.setattr(module, '_reconcile', one_cycle, raising=False)
    with pytest.raises(RuntimeError, match='owned failure concurrent with shutdown'):
        await asyncio.wait_for(case.run(), 1)
    assert case.events[-3:] == ['mcp-close', 'controllers-close', 'device-exit']


@pytest.mark.asyncio
async def test_cancelled_mcp_reconcile_keeps_owner_until_cleanup_finishes(tmp_path):
    from atellagent_client.runtime.mcp_reconciliation import MCPRuntimeReconciler
    from uuid import uuid4
    request = uuid4()
    selection = {'request_id': request, 'fixture': 'owned'}
    selected = {request: selection}
    manager = MCPRuntimeReconciler(tmp_path)
    manager.selections = SimpleNamespace(load=lambda:selected.copy())
    manager._publish = lambda *_args, **_kwargs: None
    started, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cleaned = []
    async def running(*_):
        started.set()
        try: await asyncio.Future()
        finally:
            cleaning.set()
            await finish.wait()
            cleaned.append(True)
    manager._run = running
    await manager.reconcile()
    child = manager.active[request][1]
    await asyncio.wait_for(started.wait(), 1)
    selected.clear()
    stopping = asyncio.create_task(manager.reconcile())
    try:
        await asyncio.wait_for(cleaning.wait(), 1)
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError): await stopping
        assert request in manager.active and not child.done(), 'Stopping owner vanished before MCP cleanup completed'
        finish.set()
        await asyncio.wait_for(manager.close(), 1)
        assert cleaned == [True] and not manager.active and child.done()
    finally:
        finish.set()
        if not stopping.done(): stopping.cancel()
        await asyncio.gather(stopping, return_exceptions=True)
        if not child.done(): child.cancel()
        await asyncio.gather(child, return_exceptions=True)


@pytest.mark.asyncio
async def test_actual_hook_controller_partial_start_releases_owned_socket_lock(case, tmp_path, monkeypatch):
    from dataclasses import replace
    from atellagent_client.integrations.agents.hook_control import HookControlRuntime
    from hook_control_support import _config, _Participant
    from native_runtime_binding_support import runtime_case
    request = case.select('codex')
    material = runtime_case(tmp_path)
    binding = replace(material.binding, request_id=request)
    config = _config();config.native_host = 'codex'
    config.platform_origin = material.case.draft.platform_origin
    config.tenant_id = str(binding.device.tenant_id)
    config.service_account_id = str(binding.service_account_id)
    config.integration_id = str(binding.integration_id)
    config.gateway_url = binding.gateway_origin
    config.cert_path = str(binding.stored.certificate_path)
    config.key_path = str(binding.stored.private_key_path)
    case.setups[request] = SimpleNamespace(config=config, binding=binding)
    participant = _Participant(config)
    entered, shutdown = asyncio.Event(), asyncio.Event()
    async def starting(): entered.set();await asyncio.Future()
    participant.start = AsyncMock(side_effect=starting)
    parent = tmp_path / 'private-hook';parent.mkdir(mode=0o700)
    endpoint = parent / 'control.sock'
    runtime = HookControlRuntime(config, socket_path=str(endpoint), native_binding=binding, participant=participant)
    case.manager.runtime_factory = lambda *_args, **_kwargs:runtime
    @asynccontextmanager
    async def device(): yield
    monkeypatch.setattr(module.DeviceRuntime, 'open', AsyncMock(side_effect=lambda _:device()))
    monkeypatch.setattr(module, 'NativeRuntimeSelections', lambda _:case.manager.selections)
    monkeypatch.setattr(module, 'NativeControllerReconciler', lambda *_:case.manager)
    monkeypatch.setattr(module, 'MCPRuntimeReconciler', lambda _:SimpleNamespace(reconcile=AsyncMock(), close=AsyncMock()))
    task = asyncio.create_task(module.run(tmp_path, endpoint=lambda _: str(endpoint), wait_for_shutdown=shutdown.wait))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert runtime._socket_lock_fd is not None
        shutdown.set()
        await asyncio.wait_for(task, 1)
        assert not case.manager.active and runtime._socket_lock_fd is None
        assert not participant.started and not runtime.started
        replacement = HookControlRuntime(config, socket_path=str(endpoint), native_binding=binding, participant=_Participant(config))
        replacement._ensure_socket_parent()
        try:
            replacement._acquire_socket_lock()
            assert replacement._socket_lock_fd is not None
        finally:
            replacement._release_socket_lock()
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.stop()
