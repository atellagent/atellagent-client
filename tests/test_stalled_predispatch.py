"""A stalled transport attempt must leave time to recover the known lease."""
import asyncio
import httpx
import pytest
from test_connected_receive_admission import owned_receiver, cleanup
from atellagent_client.connected import participant_receiver as receiver

@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['verification','acknowledgement'])
async def test_stalled_first_attempt_recovers_same_delivery_before_shared_deadline(monkeypatch,phase):
    monkeypatch.setattr(receiver,'_PRE_DISPATCH_RECOVERY_SECONDS',.3)
    monkeypatch.setattr(receiver,'_PRE_DISPATCH_ATTEMPT_SECONDS',.02,raising=False)
    c=owned_receiver(monkeypatch,limit=1)
    calls=[]
    async def operation(message,*args):
        calls.append((message.message_id,message.lease.lease_id))
        if len(calls)==1:await asyncio.Event().wait()
    if phase=='verification':c.p._validator.validate.side_effect=operation
    else:c.p._acknowledge.side_effect=operation
    try:
        await asyncio.wait_for(c.p._receive_once(),.6)
        await asyncio.wait_for(c.running.wait(),.3)
        assert len(calls)==2 and calls[0]==calls[1]
        assert len(c.requests)==len(c.started)==1
    finally:await cleanup(c)

@pytest.mark.asyncio
async def test_predispatch_http2_disconnect_retries_known_delivery_only(monkeypatch):
    c=owned_receiver(monkeypatch,limit=1)
    calls=[]
    async def operation(message):
        calls.append((message.message_id,message.lease.lease_id))
        if len(calls)==1:raise httpx.RemoteProtocolError('owned HTTP2 connection ended')
    c.p._validator.validate.side_effect=operation
    try:
        await c.p._receive_once()
        await asyncio.wait_for(c.running.wait(),.3)
        assert len(calls)==2 and calls[0]==calls[1]
        assert len(c.requests)==len(c.started)==1
    finally:await cleanup(c)
