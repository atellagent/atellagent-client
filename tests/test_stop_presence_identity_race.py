"""Stop retains its exact cleanup owner when an in-flight receive is denied."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
import pytest
from atellagent_client.connected.participant import ConnectedParticipant
from atellagent_client.connected.participant_protocol import ConnectedHTTPError
from connected_runtime_support import _config,_Response

@pytest.mark.asyncio
async def test_receive_403_during_drain_cannot_erase_deregistration_owner(monkeypatch):
 config=_config();original_id=str(uuid4());receive_entered=asyncio.Event();drain_sent=asyncio.Event();calls=[]
 monkeypatch.setattr('atellagent_client.connected.participant.ConnectedCapabilityValidator',lambda *_:object())
 async def request(method,url,**kwargs):
  calls.append((method,url))
  if method=='DELETE':return _Response(204)
  drain_sent.set()
  async with asyncio.timeout(1):
   while participant.instance_id is not None:await asyncio.sleep(0)
  return _Response(200,{'instance_id':original_id,'presence_status':'draining','drain_requested_at':'fixture'})
 session=SimpleNamespace(base_url=config.gateway_url,request_authenticated=request)
 participant=ConnectedParticipant(config,session=session);participant._instance_id=original_id;participant._started=True
 async def receive():
  receive_entered.set();await drain_sent.wait();raise ConnectedHTTPError(403,'fixture denial during drain')
 monkeypatch.setattr(participant,'_receive_once',receive)
 receiver=asyncio.create_task(participant._receive_loop());participant._loop_tasks=[receiver];await receive_entered.wait()
 await asyncio.wait_for(participant.stop(grace_seconds=0),2)
 assert calls==[('POST',config.gateway_url+config.drain_path_template.format(instance_id=original_id)),('DELETE',config.gateway_url+config.deregistration_path_template.format(instance_id=original_id))]
 assert receiver.done()and not participant._loop_tasks and participant.instance_id is None and not participant._started
