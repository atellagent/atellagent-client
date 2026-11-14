import asyncio
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from unittest.mock import AsyncMock
import pytest
from connected_runtime_support import _message
from test_connected_receive_admission import owned_receiver,cleanup
from atellagent_client.connected.participant_protocol import ConnectedHTTPError
from atellagent_client.connected.contracts import parse_connected_message

@pytest.mark.asyncio
async def test_temporary_ack_failure_retries_same_lease_and_waits_before_handler(monkeypatch):
    c=owned_receiver(monkeypatch,limit=1);second=asyncio.Event();release=asyncio.Event();seen=[]
    async def ack(message,*_args):
        seen.append((message.message_id,message.lease.lease_id))
        if len(seen)==1:raise ConnectedHTTPError(503,'synthetic queue outage')
        second.set();await release.wait()
    monkeypatch.setattr(c.p,'_acknowledge',ack)
    task=asyncio.create_task(c.p._receive_once())
    try:
        await asyncio.wait_for(second.wait(),1)
        assert c.started==[]and not task.done()and seen[0]==seen[1]
        release.set();await task;await asyncio.wait_for(c.running.wait(),1)
        assert len(c.started)==1 and len(seen)==2
    finally:
        release.set();await cleanup(c,task)

@pytest.mark.asyncio
@pytest.mark.parametrize('status',[400,401,403,404,409,413])
async def test_denied_ack_never_retries_or_starts_handler(monkeypatch,status):
    c=owned_receiver(monkeypatch);c.p._acknowledge.side_effect=ConnectedHTTPError(status,'synthetic denial')
    try:
        with pytest.raises(ConnectedHTTPError):await c.p._receive_once()
        assert c.p._acknowledge.await_count==1 and c.started==[]
    finally:await cleanup(c)

@pytest.mark.asyncio
@pytest.mark.parametrize('status',[502,503,504])
async def test_temporary_ack_retry_remains_finite_without_handler(monkeypatch,status):
    c=owned_receiver(monkeypatch);c.p._acknowledge.side_effect=ConnectedHTTPError(status,'synthetic outage')
    try:
        with pytest.raises(ConnectedHTTPError):await asyncio.wait_for(c.p._receive_once(),2)
        assert c.p._acknowledge.await_count==4 and c.started==[]
    finally:await cleanup(c)

@pytest.mark.asyncio
async def test_ack_retry_does_not_extend_original_lease_deadline(monkeypatch):
    c=owned_receiver(monkeypatch);message=parse_connected_message(_message()['message'])
    message=replace(message,lease=replace(message.lease,expires_at=datetime.now(timezone.utc)+timedelta(milliseconds=50)))
    c.p._acknowledge.side_effect=ConnectedHTTPError(503,'synthetic outage')
    try:
        with pytest.raises(TimeoutError):await asyncio.wait_for(c.p._validate_and_acknowledge(message),.3)
        assert c.p._acknowledge.await_count==1 and c.started==[]
    finally:await cleanup(c)
