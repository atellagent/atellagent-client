# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Native hook protocol quota behavior with real signed instructions."""
from datetime import datetime, timedelta, timezone
import json
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

from atellagent_client.integrations.agents.control_gateway_errors import ControlAdmissionError
from atellagent_client.integrations.agents.hook_control_protocol import HookControlError
from atellagent_client.pep.quota_state import QuotaStateCache, QUOTA_CODE
from atellagent_client.pep.contracts import raw_action_fingerprint
from hook_control_support import HookControlRuntimeCase


class HookQuotaTests(HookControlRuntimeCase):
    def install_quota(self, mode):
        private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
        jwk['kid']='quota-test'
        binding=self.runtime._native_binding
        authority={'authorization_id':str(binding.authorization_id),'authorization_epoch':binding.authorization_epoch}
        now=datetime.now(timezone.utc)
        claims={'iss':'gateway','aud':['atellagent-client-quota-state'],'typ':'atellagent_quota_state','schema_version':'v1',
            'sub':self.config.integration_id,'tenant_id':self.config.tenant_id,'integration_id':self.config.integration_id,
            'service_account_id':self.config.service_account_id,'surface':'native_hook','surfaces':['native_hook'],'connection_revision':1,'capability_code':'governed_action_decision',
            'state':'exhausted','disposition':mode,'entitlement_revision':1,'settings_revision':2,
            'iat':int(time()),'exp':int(time())+60,'jti':'fixture-quota',
            'period_start':(now-timedelta(days=1)).isoformat(),'period_end':(now+timedelta(days=1)).isoformat(),**authority}
        token=jwt.encode(claims,private,algorithm='RS256',headers={'kid':'quota-test'})
        self.cache=QuotaStateCache(self.config,surface='native_hook',fetch=AsyncMock(),authorization_binding=lambda:authority)
        self.cache._jwks=SimpleNamespace(get=AsyncMock(return_value={'keys':[jwk]}))
        self.governance.quota_state_cache=lambda _:self.cache
        self.governance.preflight_async=AsyncMock(side_effect=ControlAdmissionError('Exhausted',QUOTA_CODE,{'quota_state_instruction':token}))
        self.governance.action_gate.enforce_local_restrictions=Mock()
        self.runtime._outbox.reserve=AsyncMock(side_effect=AssertionError('Unadmitted action entered outbox'))

    def params(self,index=0):
        return {**self._turn_fields(),'tool_call_id':'quota-'+str(index),'tool_name':'shell.execute',
                'arguments':{'command':'pwd'},'adapter_version':'atellagent.host-hooks.v1'}

    async def test_one_hundred_local_actions_have_one_preflight_and_no_outbox_or_cloud_outcomes(self):
        self.install_quota('passthrough')
        for index in range(100):
            params=self.params(index)
            result=await self.client.call('action.preflight',params)
            self.assertTrue(result['allowed'])
            self.assertFalse(result['central_policy_evaluated'])
            self.assertNotIn('decision_id',result)
            observed=raw_action_fingerprint(action=params['tool_name'],facts=params['arguments'],
                raw_context={'host':params['host'],'adapter_version':params['adapter_version']})
            outcome=await self.client.call('action.postflight',{**self._turn_fields(),
                'tool_call_id':params['tool_call_id'],'success':True,'observed_action_fingerprint':observed})
            self.assertEqual(outcome,{'recorded':False,'ignored_reason':'quota_unadmitted','central_audit':False})
        self.assertEqual(self.governance.preflight_async.await_count,1)
        self.assertEqual(self.governance.outcomes,[])
        self.assertEqual(self.runtime._quota_unadmitted,{})
        self.runtime._outbox.reserve.assert_not_called()
        self.assertEqual(self.governance.action_gate.enforce_local_restrictions.call_count,100)

    async def test_stop_is_local_after_first_typed_response(self):
        self.install_quota('stop')
        for index in range(10):
            with self.assertRaisesRegex(HookControlError,QUOTA_CODE):
                await self.client.call('action.preflight',self.params(index))
        self.assertEqual(self.governance.preflight_async.await_count,1)
        self.governance.action_gate.enforce_local_restrictions.assert_not_called()
        self.runtime._outbox.reserve.assert_not_called()

    async def test_quota_passthrough_keeps_local_restrictions(self):
        from atellagent_client.governance.actions import PolicyActionDenied
        self.install_quota('passthrough')
        self.governance.action_gate.enforce_local_restrictions.side_effect=PolicyActionDenied('local_denied')
        result=await self.client.call('action.preflight',self.params())
        self.assertFalse(result['allowed'])
        self.assertEqual(result['reason_code'],'local_policy_denied')
        self.runtime._outbox.reserve.assert_not_called()
        self.assertEqual(self.governance.outcomes,[])
