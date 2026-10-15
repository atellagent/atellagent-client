# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Governed model and MCP calls available during one connected agent delivery."""

import json
from contextvars import ContextVar
from atellagent_client.sdk.client_modules.mcp_tools import _mcp_tool_result


_ACTIONS = ContextVar('atellagent_connected_agent_actions', default=None)


def _bind_agent_actions(actions):
    return _ACTIONS.set(actions)


def _reset_agent_actions(token):
    _ACTIONS.reset(token)


def _current():
    actions = _ACTIONS.get()
    if actions is None:
        raise RuntimeError('Agent actions are available only during a connected delivery')
    return actions


class AgentParticipantActions:
    """Never derive user or workflow authority from customer-provided metadata."""

    async def invoke_model_async(self, messages, **options):
        return await _current().invoke_model_async(messages, **options)

    async def emit_agent_event_async(self, **options):
        """Report runtime observations as this agent, not as execution authority."""
        result = await _current().emit_agent_event_async(**options)
        if not result.get('success'):
            raise RuntimeError(f"Agent event ingest failed ({result.get('status')})")
        return result

    async def call_mcp_tool_result_async(self, target_binding, tool_name, arguments, *,
                                       tool_call_id, action_context=None, poll_timeout_seconds=30):
        actions = _current()
        if not tool_call_id or len(tool_call_id) > 128:
            raise ValueError('A bounded model-generated tool_call_id is required')
        request = {
            'source_agent': actions._context.config.service_account_id,
            'target_agent': target_binding, 'task_type': 'mcp_tool',
            'message': json.dumps({'tool_name': tool_name, 'tool_call_id': tool_call_id,
                                   'arguments': arguments}, allow_nan=False),
            'context': {'tool_name': tool_name, 'tool_call_id': tool_call_id},
            'timeout': poll_timeout_seconds,
        }
        # Connected effects use the immutable delivery/tool-call identity below,
        # not the standalone caller's action key. Do not accept other authority.
        if action_context and set(action_context) != {'action_key'}:
            raise ValueError('Connected agent actions do not accept caller authority context')
        result = await actions.invoke_mcp(
            effect_key=f'{actions._context.message.message_id}:tool:{tool_call_id}', request=request)
        if result.get('success') is False:
            raise RuntimeError(result.get('error') or 'MCP tool call failed')
        return _mcp_tool_result(result)


__all__ = ['AgentParticipantActions']
