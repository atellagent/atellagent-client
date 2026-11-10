"""Connected participant lifecycle owns every loop through interruption."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from connected_runtime_support import _config, _Response
from atellagent_client.connected.participant import ConnectedParticipant


def owned_participant(monkeypatch, *, pause_registration=False, pause_drain=False,
                      pause_loop_cleanup=False, partial_registration=False):
    registration, register_release, drain, drain_release, cleanup, finish = (
        asyncio.Event() for _ in range(6))
    config = _config()
    session = SimpleNamespace(base_url=config.gateway_url, close_async=AsyncMock())
    monkeypatch.setattr('atellagent_client.connected.participant.GatewaySession.from_service_account_config',
                        lambda *_args, **_kwargs: session)
    monkeypatch.setattr('atellagent_client.connected.participant.ConnectedCapabilityValidator',
                        lambda *_args: object())
    participant = ConnectedParticipant(config)
    owners, cleaned, registrations, requests = [], [], [], []

    async def register():
        registrations.append(asyncio.current_task())
        if partial_registration:
            participant._instance_id = 'owned-instance'
        registration.set()
        if pause_registration:
            await register_release.wait()
        participant._instance_id = 'owned-instance'

    async def loop():
        owner = asyncio.current_task()
        owners.append(owner)
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            if pause_loop_cleanup:
                await finish.wait()
            cleaned.append(owner)

    async def request(method, path, **_kwargs):
        requests.append((method, path))
        if method == 'DELETE':
            return _Response(204)
        drain.set()
        if pause_drain:
            await drain_release.wait()
        return _Response(200, {'instance_id':'owned-instance', 'presence_status':'draining',
                               'drain_requested_at':'owned-fixture'})

    monkeypatch.setattr(participant, '_register', register)
    monkeypatch.setattr(participant, '_receive_loop', loop)
    monkeypatch.setattr(participant, '_heartbeat_loop', loop)
    monkeypatch.setattr(participant, '_request', request)
    return SimpleNamespace(p=participant, session=session, registration=registration,
        register_release=register_release, drain=drain, drain_release=drain_release,
        cleanup=cleanup, finish=finish, owners=owners, cleaned=cleaned,
        registrations=registrations, requests=requests)


async def finish_fixture(c, *tasks):
    c.register_release.set()
    c.drain_release.set()
    c.finish.set()
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await c.p.stop(grace_seconds=0)
    # Include every exact fixture owner, including tasks the old implementation lost.
    for task in c.owners:
        if not task.done():
            task.cancel()
    await asyncio.gather(*c.owners, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("stalled_method", ["POST", "DELETE"])
async def test_remote_cleanup_timeout_does_not_prevent_local_release(monkeypatch, stalled_method):
    c = owned_participant(monkeypatch)
    request = c.p._request
    canceled = asyncio.Event()
    async def slow_request(method, path, **kwargs):
        if method == stalled_method:
            try:
                await asyncio.Future()
            finally:
                canceled.set()
        return await request(method, path, **kwargs)
    c.p._request = slow_request
    await c.p.start()
    await asyncio.sleep(0)
    await asyncio.wait_for(c.p.stop(grace_seconds=0), 3)
    assert canceled.is_set() and not c.p._loop_tasks and c.p.instance_id is None
    assert set(c.cleaned) == set(c.owners)
    c.session.close_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_start_creates_one_tracked_loop_pair(monkeypatch):
    c = owned_participant(monkeypatch, pause_registration=True)
    first = asyncio.create_task(c.p.start())
    second = None
    try:
        await asyncio.wait_for(c.registration.wait(), 1)
        second = asyncio.create_task(c.p.start())
        await asyncio.sleep(.02)
        c.register_release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        await asyncio.sleep(0)
        assert len(c.registrations) == 1
        assert len(c.owners) == 2 and set(c.owners) == c.p._loop_tasks
        await c.p.stop(grace_seconds=0)
        assert set(c.owners) == set(c.cleaned)
        assert not c.p._loop_tasks
    finally:
        await finish_fixture(c, first, *([second] if second else []))


@pytest.mark.asyncio
async def test_stop_during_start_joins_registration_and_then_cleans_up(monkeypatch):
    c = owned_participant(monkeypatch, pause_registration=True)
    start = asyncio.create_task(c.p.start())
    stop = None
    try:
        await asyncio.wait_for(c.registration.wait(), 1)
        stop = asyncio.create_task(c.p.stop(grace_seconds=0))
        await asyncio.sleep(.02)
        assert not stop.done(), 'Stop returned before startup could publish loops'
        c.session.close_async.assert_not_awaited()
        c.register_release.set()
        await asyncio.wait_for(asyncio.gather(start, stop), 1)
        assert not c.p._started and c.p.instance_id is None
        assert all(task.done() for task in c.owners)
        c.session.close_async.assert_awaited_once()
    finally:
        await finish_fixture(c, start, *([stop] if stop else []))


@pytest.mark.asyncio
async def test_cancelled_stop_finishes_drain_loops_and_owned_session(monkeypatch):
    c = owned_participant(monkeypatch, pause_drain=True)
    await c.p.start()
    await asyncio.sleep(0)
    stop = asyncio.create_task(c.p.stop(grace_seconds=0))
    try:
        await asyncio.wait_for(c.drain.wait(), 1)
        stop.cancel()
        await asyncio.sleep(.02)
        assert not stop.done(), 'Cancellation abandoned participant shutdown'
        c.drain_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(stop, 1)
        assert set(c.owners) == set(c.cleaned)
        assert c.p.instance_id is None and not c.p._started and not c.p._loop_tasks
        assert [v[0] for v in c.requests] == ['POST', 'DELETE']
        c.session.close_async.assert_awaited_once()
    finally:
        await finish_fixture(c, stop)


@pytest.mark.asyncio
async def test_cancelled_stop_does_not_cancel_loop_finalizers_twice(monkeypatch):
    c = owned_participant(monkeypatch, pause_loop_cleanup=True)
    await c.p.start()
    await asyncio.sleep(0)
    stop = asyncio.create_task(c.p.stop(grace_seconds=0))
    try:
        await asyncio.wait_for(c.cleanup.wait(), 1)
        stop.cancel()
        await asyncio.sleep(.02)
        assert not stop.done(), 'Stopping task lost in-progress loop cleanup'
        c.finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(stop, 1)
        assert len(c.cleaned) == 2
        c.session.close_async.assert_awaited_once()
    finally:
        await finish_fixture(c, stop)


@pytest.mark.asyncio
async def test_cancelled_partial_start_deregisters_and_closes_owned_session(monkeypatch):
    c = owned_participant(monkeypatch, pause_registration=True, partial_registration=True)
    start = asyncio.create_task(c.p.start())
    try:
        await asyncio.wait_for(c.registration.wait(), 1)
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(start, 1)
        assert c.p.instance_id is None and not c.p._started
        assert [v[0] for v in c.requests] == ['POST', 'DELETE']
        c.session.close_async.assert_awaited_once()
    finally:
        await finish_fixture(c, start)


@pytest.mark.asyncio
async def test_repeated_stop_closes_once_and_explicit_start_can_restart(monkeypatch):
    c = owned_participant(monkeypatch)
    try:
        await c.p.start()
        await asyncio.sleep(0)
        await asyncio.gather(c.p.stop(grace_seconds=0), c.p.stop(grace_seconds=0))
        c.session.close_async.assert_awaited_once()
        assert not c.p._loop_tasks
        await c.p.start()
        await asyncio.sleep(0)
        assert c.p._started and len(c.p._loop_tasks) == 2
        await c.p.stop(grace_seconds=0)
        assert c.session.close_async.await_count == 2
        assert len(c.owners) == 4 and set(c.owners) == set(c.cleaned)
    finally:
        await finish_fixture(c)


@pytest.mark.asyncio
async def test_stop_leaves_injected_session_owned_by_the_caller(monkeypatch):
    c = owned_participant(monkeypatch)
    c.p._owns_session = False
    try:
        await c.p.start()
        await asyncio.sleep(0)
        await c.p.stop(grace_seconds=0)
        assert len(c.cleaned) == 2 and not c.p._loop_tasks
        c.session.close_async.assert_not_awaited()
    finally:
        await finish_fixture(c)


@pytest.mark.asyncio
async def test_concurrent_certificate_reloads_serialize_session_and_loop_replacement(monkeypatch):
    c = owned_participant(monkeypatch)
    second, third = (SimpleNamespace(close_async=AsyncMock()) for _ in range(2))
    replacements = iter([second, third])
    monkeypatch.setattr('atellagent_client.connected.participant.GatewaySession.from_service_account_config',
                        lambda *_args, **_kwargs: next(replacements))
    try:
        await c.p.start()
        await asyncio.sleep(0)
        await asyncio.gather(c.p.reload_client_certificate(grace_seconds=0),
                             c.p.reload_client_certificate(grace_seconds=0))
        await asyncio.sleep(0)
        assert c.p.session is third and c.p._started
        assert len(c.registrations) == 3 and len(c.p._loop_tasks) == 2
        c.session.close_async.assert_awaited_once()
        second.close_async.assert_awaited_once()
        third.close_async.assert_not_awaited()
        await c.p.stop(grace_seconds=0)
        third.close_async.assert_awaited_once()
        assert set(c.owners) == set(c.cleaned)
    finally:
        await finish_fixture(c)


@pytest.mark.asyncio
async def test_cancelled_stop_joins_actual_delivery_handler_and_renewal(monkeypatch):
    from connected_runtime_support import _message
    from atellagent_client.connected.contracts import parse_connected_message
    c = owned_participant(monkeypatch)
    handler_ready, renewal_ready, finalizing, finish = (asyncio.Event() for _ in range(4))
    children, completed = [], []

    async def handler(*_args):
        children.append(asyncio.current_task())
        handler_ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            finalizing.set()
            await finish.wait()
            completed.append(asyncio.current_task())

    async def renewal(*_args):
        children.append(asyncio.current_task())
        renewal_ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            completed.append(asyncio.current_task())

    monkeypatch.setattr(c.p, '_renew_lease', renewal)
    commit = AsyncMock()
    monkeypatch.setattr(c.p, '_commit_result', commit)
    await c.p.start()
    delivery = asyncio.create_task(c.p._process(parse_connected_message(_message()['message']), handler))
    c.p._track_delivery(delivery)
    stop = None
    try:
        await asyncio.wait_for(asyncio.gather(handler_ready.wait(), renewal_ready.wait()), 1)
        stop = asyncio.create_task(c.p.stop(grace_seconds=0))
        await asyncio.wait_for(finalizing.wait(), 1)
        stop.cancel()
        stop.cancel()
        await asyncio.sleep(.02)
        assert not stop.done() and not delivery.done()
        assert delivery in c.p._delivery_tasks
        c.session.close_async.assert_not_awaited()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(stop, 1)
        assert delivery.done() and not c.p._delivery_tasks
        assert len(children) == 2 and set(children) == set(completed)
        c.session.close_async.assert_awaited_once()
        commit.assert_not_awaited()
    finally:
        finish.set()
        await finish_fixture(c, *([stop] if stop else []))
        if not delivery.done():
            delivery.cancel()
        await asyncio.gather(delivery, *children, return_exceptions=True)
