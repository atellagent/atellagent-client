# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Verified quota scope, local suppression and single-flight recovery."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
import pytest

from atellagent_client.pep.quota_state import QuotaStateCache, QuotaStateUnavailable, quota_error_detail


@pytest.fixture
def fixture():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='test-key', alg='RS256', use='sig')
    config = SimpleNamespace(tenant_id='tenant-a', integration_id='integration-a', service_account_id='account-a',
        gateway_url='https://gateway.example', api_version='v1', cert_path=None, key_path=None, timeout=1)
    fetch = AsyncMock()
    cache = QuotaStateCache(config, surface='native_hook', fetch=fetch)
    cache._jwks = SimpleNamespace(get=AsyncMock(return_value={'keys':[jwk]}))

    def token(**changes):
        now = datetime.now(timezone.utc)
        claims = {'iss':'gateway','aud':['atellagent-client-quota-state'],'typ':'atellagent_quota_state','schema_version':'v1',
            'sub':'integration-a','tenant_id':'tenant-a','integration_id':'integration-a','service_account_id':'account-a',
            'surface':'native_hook','surfaces':['native_hook'],'connection_revision':1,'capability_code':'governed_action_decision','state':'exhausted','disposition':'passthrough',
            'entitlement_revision':2,'settings_revision':3,'period_start':(now-timedelta(days=1)).isoformat(),
            'period_end':(now+timedelta(days=1)).isoformat(),'iat':int(time()),'exp':int(time())+60,'jti':'test-observation',
            'authorization_id':None,'authorization_epoch':None, **changes}
        return jwt.encode(claims, key, algorithm='RS256', headers={'kid':'test-key'})
    return cache,fetch,token


@pytest.mark.asyncio
async def test_valid_instruction_suppresses_one_hundred_actions_without_per_action_state(fixture):
    cache,fetch,token = fixture
    encoded=token()
    view=await cache.accept_error(429, {'detail':{'error':'entitlement_denied','code':'governed_action_decision_quota_exhausted','quota_state_instruction':encoded}})
    assert view.continues_locally
    before=set(vars(cache))
    for _ in range(100):
        assert (await cache.before_action()).continues_locally
    assert set(vars(cache)) == before
    assert cache._pending is None
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_parallel_expiry_recovers_once_and_available_state_requires_normal_admission(fixture):
    cache,fetch,token=fixture
    await cache.accept_instruction(token())
    cache._state=replace(cache._state,expires_at=int(time())-1)
    entered,release=asyncio.Event(),asyncio.Event()
    async def recover(surface):
        assert surface=='native_hook'
        entered.set()
        await release.wait()
        return httpx.Response(200,json={'quota_state_instruction':token(state='available',disposition='stop')})
    fetch.side_effect=recover
    calls=[asyncio.create_task(cache.before_action()) for _ in range(100)]
    await entered.wait()
    release.set()
    assert await asyncio.gather(*calls)==[None]*100
    assert fetch.await_count==1
    assert await cache.before_action() is None


@pytest.mark.asyncio
async def test_stop_and_expired_passthrough_never_continue_locally(fixture):
    cache,fetch,token=fixture
    value=await cache.accept_instruction(token(disposition='stop'))
    assert not value.continues_locally
    await cache.accept_instruction(token())
    cache._state=replace(cache._state,expires_at=int(time())-1)
    fetch.side_effect=httpx.ConnectError('unavailable')
    for _ in range(10):
        with pytest.raises((QuotaStateUnavailable,httpx.ConnectError)):
            await cache.before_action()
    assert fetch.await_count==1
    assert cache.current() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'tenant_id':'other'},{'integration_id':'other'},{'service_account_id':'other'},
    {'surface':'mcp_pep'},{'typ':'atellagent_control_directive'}, {'aud':['atellagent-client-control']},
    {'capability_code':'workflow_execution'}, {'exp':int(time())-1}, {'exp':int(time())+1000},
    {'settings_revision':True}, {'entitlement_revision':0}, {'disposition':'allow'},
    {'authorization_id':'other','authorization_epoch':1}, {'period_end':'invalid'},
])
async def test_invalid_or_mismatched_instructions_never_authorize(fixture,changes):
    cache,fetch,token=fixture
    with pytest.raises(QuotaStateUnavailable):
        await cache.accept_instruction(token(**changes))
    assert cache.current() is None
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_human_grant_change_invalidates_cached_authority(fixture):
    cache,fetch,token=fixture
    binding={'authorization_id':'grant-a','authorization_epoch':1}
    cache.authorization_binding=lambda:binding
    assert (await cache.accept_instruction(token(**binding))).continues_locally
    binding['authorization_epoch']=2
    assert cache.current() is None
    fetch.assert_not_called()


@pytest.mark.parametrize('status,payload', [
    (429,{'code':'rate_limited'}),(403,{'error':'entitlement_denied','code':'governed_action_decision_quota_exhausted'}),
    (429,{'violation_type':'policy_deny','error':'entitlement_denied','code':'governed_action_decision_quota_exhausted'}),
    (429,{'violation_type':'approval_required','detail':{'error':'entitlement_denied','code':'governed_action_decision_quota_exhausted'}}),
    (500,{'error':'entitlement_denied','code':'governed_action_decision_quota_exhausted'}),
])
def test_other_failures_are_not_quota_instructions(status,payload):
    assert quota_error_detail(status,payload) is None


@pytest.mark.asyncio
async def test_newer_stop_cannot_be_rolled_back_by_old_valid_instruction(fixture):
    cache,fetch,token=fixture
    await cache.accept_instruction(token(disposition='stop',settings_revision=4,connection_revision=2))
    assert not (await cache.accept_instruction(token())).continues_locally
    assert (await cache.before_action()).disposition=='stop'
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_native_model_and_tool_surfaces_share_one_recovery_flight(fixture):
    from atellagent_client.integrations.agents.control_quota import ControlQuotaMixin
    cache,fetch,token=fixture
    cache.config.integration_type='agent'
    cache.config.native_host='codex'
    cache.config.capabilities=['agent.control']
    class Governance(ControlQuotaMixin):
        config=cache.config
        _quota_states={}
        _quota_authorization_binding=None
        gateway_session=SimpleNamespace(base_url='https://gateway.example',http_client_manager=object())
    governance=Governance()
    tool=governance.quota_state_cache('native_hook')
    model=governance.quota_state_cache('local_model')
    assert tool is model
    tool._jwks=cache._jwks
    await tool.accept_instruction(token(surfaces=['native_hook','local_model']))
    tool._state=replace(tool._state,expires_at=int(time())-1)
    entered,release=asyncio.Event(),asyncio.Event()
    async def recover(*args,**kwargs):
        entered.set()
        await release.wait()
        return httpx.Response(200,json={'quota_state_instruction':token(state='available',disposition='stop',surfaces=['native_hook','local_model'])})
    governance._request_gateway_async=AsyncMock(side_effect=recover)
    requests=[asyncio.create_task((tool if index%2 else model).before_action()) for index in range(100)]
    await entered.wait()
    release.set()
    assert await asyncio.gather(*requests)==[None]*100
    assert governance._request_gateway_async.await_count==1


@pytest.mark.asyncio
async def test_one_hundred_local_model_invocations_have_one_cloud_decision_and_no_central_receipts(fixture):
    from atellagent_client.integrations.agents.control_quota import ControlQuotaMixin
    from atellagent_client.integrations.agents.control_model_decisions import model_decision_async
    from atellagent_client.integrations.providers.session import DecisionModelTransport
    from atellagent_client.protocol.agent_contracts import ModelDecisionRequest
    from atellagent_client.governance.actions import RuntimeActionGate
    cache,fetch,token=fixture
    cache.config.integration_type='agent'
    cache.config.native_host=None
    cache.config.capabilities=['agent.process']
    class Governance(ControlQuotaMixin):
        config=cache.config
        identity_mode='boundary_identity_only'
        _quota_states={}
        _quota_authorization_binding=None
        gateway_session=SimpleNamespace(base_url='https://gateway.example',http_client_manager=object())
        action_gate=RuntimeActionGate(source='cluster_directive')
        def _merge_context(self,**kwargs):
            return {}
        async def model_decision_async(self,request,**kwargs):
            return await model_decision_async(self,request=request,**kwargs)
    governance=Governance()
    local_cache=governance.quota_state_cache('local_model')
    local_cache._jwks=cache._jwks
    encoded=token(surface='local_model',surfaces=['local_model'])
    governance._request_gateway_async=AsyncMock(return_value=httpx.Response(429,json={
        'detail':{'error':'entitlement_denied','code':'governed_action_decision_quota_exhausted','quota_state_instruction':encoded}}))
    request=ModelDecisionRequest(input_scope='turn_entry',messages=[{'role':'user','content':'harmless'}])
    transport=DecisionModelTransport(governance)
    provider=AsyncMock(return_value={'text':'local mock'})
    admitted=[]
    for _ in range(100):
        decision,value=await transport.invoke(decision_request=request,invoke=provider,on_admitted=admitted.append)
        assert decision.enforcement=='advisory' and decision.decision_id==''
        assert set(decision.evaluated.values())=={'unevaluated'}
        assert value=={'text':'local mock'}
    assert governance._request_gateway_async.await_count==1
    assert provider.await_count==100
    assert admitted==[]
