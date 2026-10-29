"""Repeated cancellation must retain independent lifecycle cleanup owners."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from atellagent_client.runtime.owned_tasks import join_owned
from atellagent_client.onboarding.process_lease import process_lease
from atellagent_client.runtime import supervisor


@pytest.mark.asyncio
async def test_repeated_cancel_joins_both_finalizers_before_return():
    ready, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    owners, finalizers, finished = [], [], []
    async def operation():
        owners.append(asyncio.current_task())
        if len(owners) == 2: ready.set()
        try:
            await asyncio.Future()
        finally:
            finalizers.append(asyncio.current_task())
            if len(finalizers) == 2: cleaning.set()
            await release.wait()
            finished.append(asyncio.current_task())
    job = asyncio.create_task(join_owned(operation(), operation()))
    try:
        await asyncio.wait_for(ready.wait(), 1)
        job.cancel()
        await asyncio.wait_for(cleaning.wait(), 1)
        job.cancel()
        await asyncio.sleep(.02)
        assert not job.done(), 'Second cancellation abandoned owned finalizers'
        assert not finished and all(not task.done() for task in owners)
        release.set()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(job, 1)
        assert set(owners) == set(finished)
    finally:
        release.set()
        if not job.done(): job.cancel()
        await asyncio.gather(job, *owners, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_child_cancellation_is_reported_after_the_other_owner_finishes():
    entered, release = asyncio.Event(), asyncio.Event()
    completed = []
    async def child_cancel():
        raise asyncio.CancelledError
    async def sibling():
        entered.set()
        await release.wait()
        completed.append(True)
    job = asyncio.create_task(join_owned(child_cancel(), sibling()))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not job.done()
        release.set()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(job, 1)
        assert completed == [True]
    finally:
        release.set()
        await asyncio.gather(job, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_parallel_close_keeps_the_actual_device_lease_until_both_finish(tmp_path, monkeypatch):
    directory = tmp_path / 'owned'; directory.mkdir(mode=0o700)
    lock = directory / '.device-runtime.lock'
    shutdown, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    entered, finished, events = [], [], []

    @asynccontextmanager
    async def device():
        with process_lease(lock) as acquired:
            assert acquired
            events.append('device-enter')
            try: yield
            finally: events.append('device-exit')

    async def close():
        entered.append(asyncio.current_task())
        if len(entered) == 2: closing.set()
        try:
            await release.wait()
        finally:
            await release.wait()
            finished.append(asyncio.current_task())

    async def reconcile(): shutdown.set()
    controllers = SimpleNamespace(reconcile=AsyncMock(), close=close)
    mcp = SimpleNamespace(reconcile=reconcile, close=close)
    monkeypatch.setattr(supervisor.DeviceRuntime, 'open', AsyncMock(side_effect=lambda _:device()))
    monkeypatch.setattr(supervisor, 'NativeRuntimeSelections', lambda _:None)
    monkeypatch.setattr(supervisor, 'NativeControllerReconciler', lambda *_:controllers)
    monkeypatch.setattr(supervisor, 'MCPRuntimeReconciler', lambda _:mcp)
    job = asyncio.create_task(supervisor.run(directory, endpoint=lambda _:'owned-unused', wait_for_shutdown=shutdown.wait))
    try:
        await asyncio.wait_for(closing.wait(), 1)
        job.cancel()
        await asyncio.sleep(.02)
        job.cancel()
        await asyncio.sleep(.02)
        assert not job.done() and events == ['device-enter']
        with process_lease(lock) as acquired:
            assert not acquired
        release.set()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(job, 1)
        assert len(finished) == 2 and events == ['device-enter', 'device-exit']
        with process_lease(lock) as acquired:
            assert acquired
    finally:
        release.set()
        if not job.done(): job.cancel()
        await asyncio.gather(job, *entered, return_exceptions=True)


@pytest.mark.asyncio
async def test_explicit_cleanup_mode_is_not_cancelled_by_repeated_parent_interrupt():
    entered, release = asyncio.Event(), asyncio.Event()
    owners, completed = [], []
    async def close():
        owners.append(asyncio.current_task())
        if len(owners) == 2: entered.set()
        await release.wait()
        completed.append(asyncio.current_task())
    job = asyncio.create_task(join_owned(close(), close(), cancel_on_interrupt=False))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        job.cancel()
        await asyncio.sleep(.01)
        job.cancel()
        await asyncio.sleep(.01)
        assert not job.done() and all(task.cancelling() == 0 for task in owners)
        release.set()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(job, 1)
        assert set(owners) == set(completed)
    finally:
        release.set()
        if not job.done(): job.cancel()
        await asyncio.gather(job, *owners, return_exceptions=True)


@pytest.mark.asyncio
async def test_interrupted_work_reports_known_failure_after_other_owner_finishes():
    ready, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    failure_error = ValueError('owned original failure')
    finished = []
    async def failure():
        raise failure_error
    async def sibling():
        ready.set()
        try: await asyncio.Future()
        finally:
            cleaning.set()
            await release.wait()
            finished.append(True)
    job = asyncio.create_task(join_owned(failure(), sibling()))
    try:
        await asyncio.wait_for(ready.wait(), 1)
        job.cancel()
        await asyncio.wait_for(cleaning.wait(), 1)
        job.cancel()
        release.set()
        with pytest.raises(ValueError, match='owned original failure') as error: await asyncio.wait_for(job, 1)
        assert error.value is failure_error and finished == [True]
    finally:
        release.set()
        if not job.done(): job.cancel()
        await asyncio.gather(job, return_exceptions=True)


@pytest.mark.asyncio
async def test_values_remain_in_input_order_and_empty_join_is_valid():
    entered, release = asyncio.Event(), asyncio.Event()
    async def first():
        entered.set()
        await release.wait()
        return 'first'
    async def second(): return 'second'
    job = asyncio.create_task(join_owned(first(), second()))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not job.done()
        release.set()
        assert await job == ['first', 'second']
        assert await join_owned() == []
    finally:
        release.set()
        await asyncio.gather(job, return_exceptions=True)


@pytest.mark.asyncio
async def test_existing_future_owner_is_cancelled_and_observed():
    future = asyncio.get_running_loop().create_future()
    job = asyncio.create_task(join_owned(future))
    await asyncio.sleep(0)
    job.cancel()
    with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(job, 1)
    assert future.cancelled()


@pytest.mark.asyncio
async def test_repeat_cancel_during_reconciliation_cleanup_preserves_remaining_phases(tmp_path, monkeypatch):
    directory = tmp_path / 'owned'; directory.mkdir(mode=0o700)
    lock = directory / '.device-runtime.lock'
    ready, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    owners, finished, closed, events = [], [], [], []

    @asynccontextmanager
    async def device():
        with process_lease(lock) as acquired:
            assert acquired
            events.append('device-enter')
            try: yield
            finally: events.append('device-exit')

    async def reconcile():
        owners.append(asyncio.current_task())
        if len(owners) == 2: ready.set()
        try: await asyncio.Future()
        finally:
            if sum(task.cancelling() > 0 for task in owners) == 2: cleaning.set()
            await release.wait()
            finished.append(asyncio.current_task())

    async def close(name):
        assert set(finished) == set(owners)
        closed.append(name)

    controllers = SimpleNamespace(reconcile=reconcile, close=lambda:close('native'))
    mcp = SimpleNamespace(reconcile=reconcile, close=lambda:close('mcp'))
    monkeypatch.setattr(supervisor.DeviceRuntime, 'open', AsyncMock(side_effect=lambda _:device()))
    monkeypatch.setattr(supervisor, 'NativeRuntimeSelections', lambda _:None)
    monkeypatch.setattr(supervisor, 'NativeControllerReconciler', lambda *_:controllers)
    monkeypatch.setattr(supervisor, 'MCPRuntimeReconciler', lambda _:mcp)
    shutdown = asyncio.Event()
    job = asyncio.create_task(supervisor.run(directory, endpoint=lambda _:'unused', wait_for_shutdown=shutdown.wait))
    try:
        await asyncio.wait_for(ready.wait(), 1)
        job.cancel()
        await asyncio.wait_for(cleaning.wait(), 1)
        job.cancel()
        await asyncio.sleep(.02)
        assert not job.done() and not closed and events == ['device-enter']
        with process_lease(lock) as acquired: assert not acquired
        release.set()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(job, 1)
        assert set(finished) == set(owners) and sorted(closed) == ['mcp', 'native']
        assert events == ['device-enter', 'device-exit']
        with process_lease(lock) as acquired: assert acquired
    finally:
        release.set()
        if not job.done(): job.cancel()
        await asyncio.gather(job, *owners, return_exceptions=True)
