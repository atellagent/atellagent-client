# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Completed effects survive bounded result transport failures without replay."""
import asyncio
import copy
from unittest.mock import AsyncMock

import httpx
import pytest

from connected_runtime_support import _Response
from test_connected_result_budget import owned_commit
from atellagent_client.connected.contracts import ConnectedHandlerResult, ConnectedProtocolError
from atellagent_client.connected.participant_protocol import ConnectedHTTPError
from atellagent_client.connected import participant_delivery as delivery


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [httpx.ConnectError('owned'), httpx.ReadError('owned'),
    httpx.ReadTimeout('owned'), httpx.RemoteProtocolError('owned'), ConnectedHTTPError(503, 'owned')])
async def test_result_retry_does_not_rerun_effect_and_keeps_renewal(monkeypatch, failure):
    c = owned_commit(monkeypatch)
    message = c.message('agent.process')
    attempted, active, stopped = [], asyncio.Event(), asyncio.Event()
    async def renew(*_):
        active.set()
        try: await asyncio.Event().wait()
        finally: stopped.set()
    c.p._renew_lease = renew
    async def request(_method, _path, **kwargs):
        assert active.is_set() and not stopped.is_set()
        attempted.append(copy.deepcopy(kwargs['json']))
        if len(attempted) == 1: raise failure
        return _Response(200, {'message_id': message.message_id, 'result_id': 'owned',
            'terminal_status': 'succeeded', 'committed_at': 'owned'})
    c.p._request = request
    result = ConnectedHandlerResult.succeeded(result_schema='owned', result_payload={'effect':'once'})
    handler = AsyncMock(return_value=result)
    await c.p._process(message, handler)
    handler.assert_awaited_once()
    assert len(attempted) == 2 and attempted[0] == attempted[1] and stopped.is_set()


@pytest.mark.asyncio
async def test_lost_commit_reply_reuses_exact_snapshot(monkeypatch):
    c = owned_commit(monkeypatch);message = c.message('agent.process')
    result = ConnectedHandlerResult.succeeded(result_schema='owned', result_payload={'effect':'original'})
    stored, attempts = [], []
    async def request(_method, _path, **kwargs):
        attempts.append(copy.deepcopy(kwargs['json']))
        if not stored:
            stored.append(copy.deepcopy(kwargs['json']))
            result.result_payload['effect'] = 'changed after handler returned'
            raise httpx.ReadError('acknowledgement lost after commit')
        assert kwargs['json'] == stored[0]
        return _Response(200, {'message_id':message.message_id,'result_id':'same-receipt',
            'terminal_status':'succeeded','committed_at':'original'})
    c.p._request = request
    await c.p._commit_result(message, result)
    assert len(stored) == 1 and len(attempts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [400, 401, 403, 404, 409, 413])
async def test_result_rejection_is_not_retried(monkeypatch, status):
    c = owned_commit(monkeypatch)
    c.p._request = AsyncMock(side_effect=ConnectedHTTPError(status, 'owned refusal'))
    with pytest.raises(ConnectedHTTPError):
        await c.p._commit_result(c.message('agent.process'), ConnectedHandlerResult.succeeded(
            result_schema='owned', result_payload={}))
    c.p._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_result_retries_and_wait_are_bounded(monkeypatch):
    c = owned_commit(monkeypatch)
    monkeypatch.setattr(delivery, '_RESULT_COMMIT_MAX_ATTEMPTS', 2)
    c.p._request = AsyncMock(side_effect=httpx.ConnectError('owned outage'))
    with pytest.raises(httpx.ConnectError):
        await c.p._commit_result(c.message('agent.process'), ConnectedHandlerResult.succeeded(
            result_schema='owned', result_payload={}))
    assert c.p._request.await_count == 2
    monkeypatch.setattr(delivery, '_RESULT_COMMIT_TIMEOUT_SECONDS', .01)
    async def stall(*_, **__): await asyncio.Event().wait()
    c.p._request = AsyncMock(side_effect=stall)
    with pytest.raises(TimeoutError):
        await c.p._commit_result(c.message('agent.process'), ConnectedHandlerResult.succeeded(
            result_schema='owned', result_payload={}))
    c.p._request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_commit_cancels_and_joins_on_lease_loss_or_stop(monkeypatch, cancel):
    c = owned_commit(monkeypatch)
    monkeypatch.setattr(delivery, '_RESULT_COMMIT_TIMEOUT_SECONDS', .05)
    entered, fail_lease, commit_stopped, renewal_stopped = (asyncio.Event()for _ in range(4))
    async def renew(*_):
        try:
            await fail_lease.wait()
            raise ConnectedHTTPError(409, 'owned lease lost')
        finally: renewal_stopped.set()
    async def request(*_, **__):
        entered.set()
        try: await asyncio.Event().wait()
        finally: commit_stopped.set()
    c.p._renew_lease, c.p._request = renew, request
    handler = AsyncMock(return_value=ConnectedHandlerResult.succeeded(result_schema='owned',result_payload={}))
    task = asyncio.create_task(c.p._process(c.message('agent.process'), handler))
    await asyncio.wait_for(entered.wait(), 1)
    if cancel: task.cancel()
    else: fail_lease.set()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await asyncio.wait_for(task, 1)
    assert commit_stopped.is_set() and renewal_stopped.is_set()
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_handler_failure_commits_failed_result_once(monkeypatch):
    c = owned_commit(monkeypatch)
    await c.p._process(c.message('agent.process'), AsyncMock(side_effect=ValueError('owned handler error')))
    assert len(c.parsed) == 1 and c.parsed[0]['terminal_status'] == 'failed'


@pytest.mark.asyncio
async def test_unconfirmed_commit_logs_message_id_without_secrets(monkeypatch, caplog):
    c = owned_commit(monkeypatch);message = c.message('agent.process')
    c.p._request = AsyncMock(side_effect=ConnectedHTTPError(403, 'private refusal body'))
    with caplog.at_level('WARNING'), pytest.raises(ConnectedHTTPError):
        await c.p._commit_result(message, ConnectedHandlerResult.succeeded(
            result_schema='owned', result_payload={'secret':'private result body'}))
    assert message.message_id in caplog.text and 'ConnectedHTTPError' in caplog.text
    record = next(r for r in caplog.records if 'commit unconfirmed' in r.message)
    assert record.connected_message_id == message.message_id
    assert record.connected_operation == message.operation
    for private in [message.lease.lease_token, 'private refusal body', 'private result body']:
        assert private not in caplog.text
