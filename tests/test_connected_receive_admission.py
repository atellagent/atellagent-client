"""Receive admission reserves local capacity before accepting another lease."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from connected_runtime_support import _config, _message, _Response
from atellagent_client.connected.participant import ConnectedParticipant
from atellagent_client.connected.contracts import ConnectedHandlerResult, ConnectedProtocolError


def owned_receiver(monkeypatch, *, limit=2, reject=False, idle=False):
    config = _config()
    session = SimpleNamespace(base_url=config.gateway_url, close_async=AsyncMock())
    monkeypatch.setattr('atellagent_client.connected.participant.ConnectedCapabilityValidator',
                        lambda *_args: SimpleNamespace(validate=AsyncMock()))
    p = ConnectedParticipant(config, session=session, max_concurrency=limit)
    release, running = asyncio.Event(), asyncio.Event()
    requests, started, finished, renewals = [], [], [], []
    p._instance_id = 'owned-instance'
    p._started = True

    async def handler(delivery, _actions):
        started.append(delivery.message_id)
        if len(started) == limit:
            running.set()
        try:
            await release.wait()
            return ConnectedHandlerResult.succeeded(result_schema='owned-result')
        finally:
            finished.append(delivery.message_id)

    async def receive(*_args, **_kwargs):
        requests.append(asyncio.current_task())
        if idle:
            return _Response(204)
        return _Response(200, _message())

    async def renew(message, _context):
        renewals.append(message.message_id)
        await asyncio.Event().wait()

    monkeypatch.setattr(p, '_request', receive)
    monkeypatch.setattr(p, '_renew_lease', renew)
    monkeypatch.setattr(p, '_commit_result', AsyncMock())
    monkeypatch.setattr(p, '_acknowledge', AsyncMock())
    p.register_handler('agent.process', handler, consequential=False)
    if reject:
        p._validator.validate.side_effect = ConnectedProtocolError('owned invalid capability')
    return SimpleNamespace(p=p, release=release, running=running, requests=requests,
        started=started, finished=finished, renewals=renewals)


async def cleanup(c, *receivers):
    for task in receivers:
        if not task.done():
            task.cancel()
    await asyncio.gather(*receivers, return_exceptions=True)
    c.release.set()
    await asyncio.gather(*tuple(c.p._delivery_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_full_handler_capacity_does_not_claim_or_accept_a_third_lease(monkeypatch):
    c = owned_receiver(monkeypatch)
    receivers = []
    try:
        for _ in range(2):
            await c.p._receive_once()
        await asyncio.wait_for(c.running.wait(), 1)
        for _ in range(20):
            receivers.append(asyncio.create_task(c.p._receive_once()))
        await asyncio.sleep(.03)
        assert len(c.requests) == 2, 'Full local capacity still receives and leases more work'
        assert len(c.p._delivery_tasks) == 2
        assert len(c.started) == 2 and len(c.renewals) == 2
        assert c.p._acknowledge.await_count == 2
    finally:
        await cleanup(c, *receivers)


@pytest.mark.asyncio
async def test_completion_frees_capacity_for_exactly_one_next_receive(monkeypatch):
    c = owned_receiver(monkeypatch, limit=1)
    receiver = None
    try:
        await c.p._receive_once()
        await asyncio.wait_for(c.running.wait(), 1)
        receiver = asyncio.create_task(c.p._receive_once())
        await asyncio.sleep(.02)
        assert not receiver.done() and len(c.requests) == 1
        c.release.set()
        await asyncio.wait_for(receiver, 1)
        await asyncio.gather(*tuple(c.p._delivery_tasks), return_exceptions=True)
        assert len(c.requests) == 2 and len(c.started) == 2 and len(c.finished) == 2
        assert c.p._commit_result.await_count == 2
    finally:
        await cleanup(c, *([receiver] if receiver else []))


@pytest.mark.asyncio
async def test_cancelled_capacity_wait_has_no_new_lease_or_acknowledgement(monkeypatch):
    c = owned_receiver(monkeypatch, limit=1)
    receiver = None
    try:
        await c.p._receive_once()
        await asyncio.wait_for(c.running.wait(), 1)
        receiver = asyncio.create_task(c.p._receive_once())
        await asyncio.sleep(.02)
        receiver.cancel()
        with pytest.raises(asyncio.CancelledError):
            await receiver
        assert len(c.requests) == 1 and len(c.p._delivery_tasks) == 1
        assert c.p._acknowledge.await_count == 1
    finally:
        await cleanup(c, *([receiver] if receiver else []))


@pytest.mark.asyncio
@pytest.mark.parametrize('idle', [False, True])
async def test_idle_or_rejected_delivery_does_not_consume_capacity(monkeypatch, idle):
    c = owned_receiver(monkeypatch, limit=1, idle=idle, reject=not idle)
    try:
        for _ in range(3):
            if idle:
                await asyncio.wait_for(c.p._receive_once(), 1)
            else:
                with pytest.raises(ConnectedProtocolError, match='owned invalid capability'):
                    await asyncio.wait_for(c.p._receive_once(), 1)
        assert len(c.requests) == 3 and not c.p._delivery_tasks and not c.started
        assert c.p._acknowledge.await_count == (0 if idle else 3)
    finally:
        await cleanup(c)


@pytest.mark.asyncio
async def test_actual_receive_loop_stops_cleanly_while_waiting_for_capacity(monkeypatch):
    c = owned_receiver(monkeypatch, limit=1)
    receive_request = c.p._request

    async def request(method, path, **kwargs):
        if path == c.p.config.drain_path_template:
            return _Response(200, {'instance_id':'owned-instance', 'presence_status':'draining',
                                   'drain_requested_at':'owned-fixture'})
        if method == 'DELETE':
            return _Response(204)
        return await receive_request(method, path, **kwargs)

    monkeypatch.setattr(c.p, '_request', request)
    receiver = asyncio.create_task(c.p._receive_loop())
    c.p._loop_tasks.add(receiver)
    try:
        await asyncio.wait_for(c.running.wait(), 1)
        await asyncio.sleep(.02)
        assert len(c.requests) == 1 and not receiver.done()
        await asyncio.wait_for(c.p.stop(grace_seconds=0), 1)
        assert receiver.done() and not c.p._loop_tasks and not c.p._delivery_tasks
        assert len(c.requests) == 1 and len(c.finished) == 1
        c.p._commit_result.assert_not_awaited()
    finally:
        await cleanup(c, receiver)


@pytest.mark.asyncio
async def test_rotation_uses_bounded_admission_and_reopens_receive_after_completion(monkeypatch):
    c = owned_receiver(monkeypatch, limit=1)
    entered, release = asyncio.Event(), asyncio.Event()
    original_request = c.p._request
    control_sent = False

    async def request(*args, **kwargs):
        nonlocal control_sent
        response = await original_request(*args, **kwargs)
        if not control_sent:
            control_sent = True
            response._payload['message'].update(kind='control', operation='certificate.rotate')
        return response

    async def rotate(_message):
        entered.set()
        await release.wait()

    monkeypatch.setattr(c.p, '_request', request)
    monkeypatch.setattr(c.p, '_perform_certificate_rotation', rotate)
    receiver = None
    try:
        await c.p._receive_once()
        await asyncio.wait_for(entered.wait(), 1)
        receiver = asyncio.create_task(c.p._receive_once())
        await asyncio.sleep(.02)
        assert len(c.requests) == 1 and len(c.p._delivery_tasks) == 1
        assert len(c.renewals) == 1 and not c.p._receive_enabled.is_set()
        release.set()
        await asyncio.wait_for(receiver, 1)
        await asyncio.wait_for(c.running.wait(), 1)
        assert len(c.requests) == 2 and len(c.p._delivery_tasks) == 1
        assert c.p._receive_enabled.is_set() and len(c.started) == 1
    finally:
        release.set()
        await cleanup(c, *([receiver] if receiver else []))


@pytest.mark.asyncio
async def test_stopped_participant_does_not_start_a_new_receive(monkeypatch):
    c = owned_receiver(monkeypatch)
    c.p._stop_event.set()
    await c.p._receive_once()
    assert not c.requests and not c.p._delivery_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['verification', 'acknowledgement'])
async def test_transient_pre_dispatch_network_failure_keeps_same_delivery(monkeypatch, phase):
    import httpx
    c = owned_receiver(monkeypatch, limit=1)
    attempts = []

    async def recover(message, *_args):
        attempts.append((message.message_id, message.lease.lease_id))
        if len(attempts) == 1:
            raise httpx.ConnectError('owned transient connection failure')

    if phase == 'verification':
        c.p._validator.validate.side_effect = recover
    else:
        c.p._acknowledge.side_effect = recover
    try:
        await c.p._receive_once()
        await asyncio.wait_for(c.running.wait(), 1)
        assert len(c.requests) == 1
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        assert len(c.started) == 1
        assert c.p._validator.validate.await_count == (2 if phase == 'verification' else 1)
        assert c.p._acknowledge.await_count == (1 if phase == 'verification' else 2)
    finally:
        await cleanup(c)


@pytest.mark.asyncio
async def test_lost_accepted_ack_response_retries_ack_without_replaying_handler(monkeypatch):
    import httpx
    c = owned_receiver(monkeypatch, limit=1)
    durable_acks = set()
    calls = []

    async def acknowledge(message, acknowledgement, *_args):
        key = (message.message_id, message.lease.lease_id, acknowledgement)
        calls.append(key)
        durable_acks.add(key)
        if len(calls) == 1:
            raise httpx.ReadError('owned response lost after acknowledgement commit')

    c.p._acknowledge.side_effect = acknowledge
    try:
        await c.p._receive_once()
        await asyncio.wait_for(c.running.wait(), 1)
        assert len(calls) == 2 and len(durable_acks) == 1
        assert len(c.requests) == 1 and len(c.started) == 1
    finally:
        await cleanup(c)


@pytest.mark.asyncio
async def test_exhausted_pre_dispatch_network_recovery_never_runs_handler(monkeypatch):
    import httpx
    c = owned_receiver(monkeypatch, limit=1)
    c.p._acknowledge.side_effect = httpx.ConnectError('owned persistent outage')
    try:
        with pytest.raises(httpx.ConnectError):
            await c.p._receive_once()
        assert c.p._acknowledge.await_count == 4
        assert len(c.requests) == 1 and not c.started and not c.p._delivery_tasks
    finally:
        await cleanup(c)


@pytest.mark.asyncio
async def test_pre_dispatch_recovery_has_shared_finite_time_budget(monkeypatch):
    c = owned_receiver(monkeypatch, limit=1)
    monkeypatch.setattr('atellagent_client.connected.participant_receiver._PRE_DISPATCH_RECOVERY_SECONDS', .02)

    async def stall(*_args):
        await asyncio.Event().wait()

    c.p._validator.validate.side_effect = stall
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(c.p._receive_once(), .5)
        assert c.p._validator.validate.await_count == 1
        assert c.p._acknowledge.await_count == 0 and not c.started
    finally:
        await cleanup(c)


@pytest.mark.asyncio
async def test_recovery_cannot_outlive_original_delivery_lease(monkeypatch):
    from datetime import datetime, timedelta, timezone
    c = owned_receiver(monkeypatch, limit=1)
    raw = _message()
    raw['message']['lease']['expires_at'] = (datetime.now(timezone.utc) + timedelta(seconds=.03)).isoformat()
    c.p._request = AsyncMock(return_value=_Response(200, raw))

    async def stall(*_args):
        await asyncio.Event().wait()

    c.p._validator.validate.side_effect = stall
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(c.p._receive_once(), .5)
        assert c.p._request.await_count == 1
        assert c.p._acknowledge.await_count == 0 and not c.started
    finally:
        await cleanup(c)


@pytest.mark.asyncio
async def test_invalid_capability_is_rejected_without_network_retry(monkeypatch):
    c = owned_receiver(monkeypatch, limit=1, reject=True)
    try:
        with pytest.raises(ConnectedProtocolError):
            await c.p._receive_once()
        assert c.p._validator.validate.await_count == 1
        assert c.p._acknowledge.await_args.args[1:] == ('rejected', 'invalid_capability')
        assert not c.started and not c.p._delivery_tasks
    finally:
        await cleanup(c)


@pytest.mark.asyncio
async def test_authority_rejection_is_not_retried_or_dispatched(monkeypatch):
    from atellagent_client.connected.participant_protocol import ConnectedHTTPError
    c = owned_receiver(monkeypatch, limit=1)
    c.p._acknowledge.side_effect = ConnectedHTTPError(403, 'owned authority revoked')
    try:
        with pytest.raises(ConnectedHTTPError):
            await c.p._receive_once()
        assert c.p._acknowledge.await_count == 1 and not c.started
    finally:
        await cleanup(c)


@pytest.mark.asyncio
async def test_cancel_during_rotation_ack_restores_receive_gate_without_handler(monkeypatch):
    c = owned_receiver(monkeypatch, limit=1)
    raw = _message()
    raw['message'].update(kind='control', operation='certificate.rotate')
    c.p._request = AsyncMock(return_value=_Response(200, raw))
    entered = asyncio.Event()

    async def stall(*_args):
        entered.set()
        await asyncio.Event().wait()

    c.p._acknowledge.side_effect = stall
    receiver = asyncio.create_task(c.p._receive_once())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not c.p._receive_enabled.is_set()
        receiver.cancel()
        with pytest.raises(asyncio.CancelledError):
            await receiver
        assert c.p._receive_enabled.is_set() and not c.p._delivery_tasks
    finally:
        await cleanup(c, receiver)
