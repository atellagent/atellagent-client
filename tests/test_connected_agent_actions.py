# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Delivery action routing and handler scope, without live credentials."""

import asyncio
import unittest
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import httpx
from atellagent_client.connected import AgentParticipantActions, mount_agent_handler
from atellagent_client.connected.actions import ConnectedActionClient, _DeliveryActionContext
from atellagent_client.connected.contracts import ConnectedProtocolError, parse_connected_message
from atellagent_client.tests.connected_runtime_support import _config, _message


class AgentActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_events_reuse_sdk_ingest_and_the_enrolled_identity(self):
        message = parse_connected_message(_message()['message'])
        calls = []
        async def request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return httpx.Response(200, json={'success': True}, request=httpx.Request(method, url))
        config = _config()
        actions = ConnectedActionClient(_DeliveryActionContext(config,
            SimpleNamespace(base_url='https://gateway.test', request_authenticated=request),
            'instance', message, message.capability))
        result = await actions.emit_agent_event_async(method='runtime_start',
            extra={'state_snapshot': {'phase': 'running'}})
        self.assertTrue(result['success'])
        self.assertEqual(calls[0][1], 'https://gateway.test/v1/agent-events/ingest')
        self.assertEqual(calls[0][2]['json']['service_account_id'], config.service_account_id)
        self.assertNotIn('X-Atellagent-Connected-Capability', calls[0][2]['headers'])

    async def test_mcp_failure_uses_shared_error_envelope_parser(self):
        message = parse_connected_message(_message()['message'])
        for payload in ({'error': 'Workflow source binding mismatch'},
                        {'detail': {'message': 'MCP admission denied'}}):
            async def request(method, url, **kwargs):
                return httpx.Response(403, json=payload, request=httpx.Request(method,url),
                    extensions={'http_version': b'HTTP/2'})
            actions = ConnectedActionClient(_DeliveryActionContext(_config(),
                SimpleNamespace(base_url='https://gateway.test',request_authenticated=request),
                'instance',message,message.capability))
            with self.assertRaises(ConnectedProtocolError) as failure:
                await actions.invoke_mcp(effect_key='tool-1',request={})
            self.assertNotIn(': None',str(failure.exception))
            self.assertIn('403',str(failure.exception))

    async def test_shared_model_operation_routes_only_to_its_delivery_and_polls_same_effect(self):
        message = parse_connected_message(_message()['message'])
        request_id = str(uuid5(NAMESPACE_URL, f'connected-model:{message.message_id}:turn-1'))
        calls = []
        async def request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            data = ({'request_id': request_id} if method == 'POST' else
                    {'result': {'output_text': 'Complete', 'tool_requests': []}})
            return httpx.Response(202 if method == 'POST' else 200, json=data,
                request=httpx.Request(method, url), extensions={'http_version': b'HTTP/2'})
        actions = ConnectedActionClient(_DeliveryActionContext(_config(),
            SimpleNamespace(base_url='https://gateway.test', request_authenticated=request),
            'instance-1', message, message.capability))
        result = await actions.invoke_model_async([{'role': 'user', 'content': 'Hello'}],
                                                  effect_key='turn-1', poll_jitter_ratio=0)
        self.assertEqual(result['output_text'], 'Complete')
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertIn(f'/messages/{message.message_id}/leases/{message.lease.lease_id}/', calls[0][1])
        self.assertEqual(calls[0][2]['json']['request']['messages'][0]['content'], 'Hello')
        self.assertEqual(set(calls[0][2]['headers']), {'X-Atellagent-Connected-Capability'})
        with self.assertRaises(ValueError):
            await actions.invoke_model_async([], effect_key='../escape')

    async def test_model_cannot_poll_a_request_from_another_delivery(self):
        message = parse_connected_message(_message()['message'])
        async def request(method, url, **kwargs):
            return httpx.Response(202, json={'request_id': 'another-delivery'},
                request=httpx.Request(method, url), extensions={'http_version': b'HTTP/2'})
        actions = ConnectedActionClient(_DeliveryActionContext(_config(),
            SimpleNamespace(base_url='https://gateway.test', request_authenticated=request),
            'instance', message, message.capability))
        with self.assertRaises(ConnectedProtocolError):
            await actions.invoke_model_async([{'role': 'user', 'content': 'Hello'}])

    async def test_handler_scope_is_reset_after_success_and_failure(self):
        participant = SimpleNamespace(register_handler=lambda operation, handler, **_: setattr(participant, 'handler', handler))
        message = parse_connected_message(_message()['message'])
        message.payload['communication_metadata'] = {'communication_id': message.idempotency_key}
        observed = []
        class Actions:
            async def invoke_model_async(self, messages, **options):
                observed.append(messages)
                return {'output_text': 'Done'}
        async def handler(payload):
            result = await AgentParticipantActions().invoke_model_async([{'role': 'user', 'content': 'Hello'}])
            return result['output_text']
        mount_agent_handler(participant, handler, consequential=False, target_idempotent=False)
        result = await participant.handler(message.delivery(), Actions())
        self.assertEqual(result.result_payload['content'], 'Done')
        self.assertEqual(len(observed), 1)
        with self.assertRaises(RuntimeError):
            await AgentParticipantActions().invoke_model_async([])
        async def failure(payload):
            raise ConnectionError('Unavailable')
        mount_agent_handler(participant, failure, consequential=False, target_idempotent=False)
        with self.assertRaises(ConnectionError):
            await participant.handler(message.delivery(), Actions())
        with self.assertRaises(RuntimeError):
            await AgentParticipantActions().invoke_model_async([])
