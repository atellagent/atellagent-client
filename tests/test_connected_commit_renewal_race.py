# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""A completed effect waits for its authoritative result receipt across a renewal race."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from connected_runtime_support import _Response
from test_connected_result_budget import owned_commit
from atellagent_client.connected.contracts import ConnectedHandlerResult
from atellagent_client.connected.participant_protocol import ConnectedHTTPError


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_status", [200, 401, 409])
async def test_commit_receipt_remains_authoritative_after_renewal_conflict(monkeypatch, commit_status):
    c = owned_commit(monkeypatch)
    message = c.message("agent.process")
    entered, renewal_failed, release = (asyncio.Event() for _ in range(3))
    calls = []

    async def renew(*_):
        await entered.wait()
        renewal_failed.set()
        raise ConnectedHTTPError(409, "lease already terminal or lost")

    async def request(*_, **kwargs):
        calls.append(kwargs["json"])
        entered.set()
        await release.wait()
        if commit_status != 200:
            raise ConnectedHTTPError(commit_status, "server refused result")
        return _Response(200, {"message_id": message.message_id, "result_id": "owned",
            "terminal_status": "succeeded", "committed_at": "owned"})

    c.p._renew_lease, c.p._request = renew, request
    handler = AsyncMock(return_value=ConnectedHandlerResult.succeeded(
        result_schema="owned", result_payload={"effect": "once"}))
    task = asyncio.create_task(c.p._process(message, handler))
    try:
        await asyncio.wait_for(renewal_failed.wait(), 1)
        # Force renewal failure to be observed before the commit reply arrives.
        await asyncio.sleep(.01)
        release.set()
        if commit_status == 200:
            await asyncio.wait_for(task, 1)
        else:
            with pytest.raises(ConnectedHTTPError) as failed:
                await asyncio.wait_for(task, 1)
            assert failed.value.status_code == commit_status
        handler.assert_awaited_once()
        assert len(calls) == 1
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
