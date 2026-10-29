# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under LICENSE.md.
"""Thin quota-state transport for the existing local action/model owners."""
import asyncio

from atellagent_client.pep.quota_state import QuotaStateCache, QUOTA_CODE
from atellagent_client.protocol.api import build_versioned_route
from atellagent_client.protocol.agent_contracts import ModelDecision
from .control_gateway_errors import ControlAdmissionError


class ControlQuotaMixin:
    def quota_state_cache(self, surface):
        caches = self._quota_states
        shared_native = (self.config.integration_type == 'agent' and bool(self.config.native_host)
            and 'agent.control' in self.config.capabilities and surface in {'native_hook','local_model'})
        # Both interception surfaces belong to one enrolled native connection.
        # Its signed scope explicitly includes both; one cache/recovery flight
        # prevents simultaneous prompt/tool hooks from doubling probe traffic.
        selected = 'native_hook' if shared_native else surface
        expected = {'native_hook','local_model'} if shared_native else {surface}
        if selected not in caches:
            async def fetch(selected):
                return await self._request_gateway_async('GET',
                    self.gateway_session.base_url + build_versioned_route(self.config.api_version, '/integrations/quota-state'),
                    workflow_context={}, params={'surface':selected})
            caches[selected] = QuotaStateCache(self.config, surface=selected, fetch=fetch, expected_surfaces=expected,
                authorization_binding=self._quota_authorization_binding,
                http_client_manager=self.gateway_session.http_client_manager)
        return caches[selected]

    async def refresh_quota_state(self, surface):
        cache = self.quota_state_cache(surface)
        await cache.recover(force=True)
        return cache.diagnostics()


def quota_cache(governance, surface):
    factory = getattr(governance, 'quota_state_cache', None)
    return factory(surface) if callable(factory) else None


def quota_refusal():
    return ControlAdmissionError("The organization's governed action allowance is exhausted.", QUOTA_CODE, {})


def local_model_result(governance, request, disposition):
    if not disposition.continues_locally:
        raise quota_refusal()
    governance.action_gate.enforce_local_restrictions(action='model.invoke', integration_type='agent',
        correlation_id=request.request_fingerprint, facts=request.to_payload())
    return ModelDecision(outcome='allow',enforcement='advisory',input_scope=request.input_scope,
        evaluated={key:'unevaluated' for key in ('identity','content','provider','model','workflow')},
        reason_code='quota_' + disposition.disposition,
        reason='Central policy checks and per-action cloud audit are paused after quota exhaustion. Local restrictions remain in effect.',
        obligations=(),valid_until=None,decision_id='',correlation_id='',request_fingerprint=request.request_fingerprint)


def run_quota_sync(coroutine):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    coroutine.close()
    raise RuntimeError('Synchronous quota control requires a synchronous caller')
