# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""SDK MCP tool invocation client methods."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional


def _mcp_tool_result(payload: Any) -> Dict[str, Any]:
    """Extract one MCP ``tools/call`` result without flattening its content.

    The general SDK methods retain their text-oriented return type for SDK
    callers.  A local MCP bridge, however, is itself an MCP server and must
    preserve the target's typed content blocks and ``isError`` signal.
    """
    raw_response = payload.get("response") if isinstance(payload, Mapping) else payload
    if isinstance(raw_response, str):
        try:
            raw_response = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise RuntimeError("MCP tool response is not JSON") from exc
    if not isinstance(raw_response, Mapping):
        raise RuntimeError("MCP tool response must be an object")
    # Gateway communication responses retain the action envelope alongside
    # the native MCP result.  Prefer that typed result over the display-ready
    # text field so an MCP bridge preserves all native content blocks.
    if isinstance(raw_response.get("mcp_result"), Mapping):
        raw_response = raw_response["mcp_result"]
    if isinstance(raw_response.get("result"), Mapping):
        raw_response = raw_response["result"]
    content = raw_response.get("content")
    if not isinstance(content, list) or not all(isinstance(item, Mapping) for item in content):
        raise RuntimeError("MCP tool response must include content blocks")
    result: Dict[str, Any] = {"content": [dict(item) for item in content]}
    if raw_response.get("isError") is True:
        result["isError"] = True
    return result


class MCPToolsClientMixin:
    def call_mcp_tool_result(
        self,
        target_binding: str,
        tool_name: str,
        arguments: Any,
        *,
        workflow_context: Optional[Dict[str, Any]] = None,
        source_agent: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        action_context: Optional[Dict[str, Any]] = None,
        poll_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 0.2,
    ) -> Dict[str, Any]:
        """Invoke a tool and return its native MCP ``tools/call`` result."""
        headers = self._apply_workflow_headers(
            {},
            workflow_context,
        )
        response = self.operations.mcp_communicate_sync(
            self._request_gateway_sync,
            headers,
            self._resolve_source_agent_id(source_agent),
            target_binding,
            tool_name,
            self._coerce_tool_arguments(arguments),
            tool_call_id=tool_call_id,
            action_context=dict(action_context or {}),
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            telemetry_emitter=self.telemetry_emitter,
            telemetry_context=self.telemetry_context,
        )
        if isinstance(response, dict) and not response.get("success", True):
            raise RuntimeError(response.get("error") or "MCP tool call failed")
        return _mcp_tool_result(response)

    def call_mcp_tool(
        self,
        target_binding: str,
        tool_name: str,
        arguments: Any,
        *,
        workflow_context: Optional[Dict[str, Any]] = None,
        source_agent: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        action_context: Optional[Dict[str, Any]] = None,
        poll_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 0.2,
    ) -> str:
        headers = self._apply_workflow_headers(
            {},
            workflow_context,
        )
        source_agent_id = self._resolve_source_agent_id(source_agent)
        tool_args = self._coerce_tool_arguments(arguments)
        response = self.operations.mcp_communicate_sync(
            self._request_gateway_sync,
            headers,
            source_agent_id,
            target_binding,
            tool_name,
            tool_args,
            tool_call_id=tool_call_id,
            action_context=dict(action_context or {}),
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            telemetry_emitter=self.telemetry_emitter,
            telemetry_context=self.telemetry_context,
        )
        if isinstance(response, dict) and not response.get("success", True):
            raise RuntimeError(response.get("error") or "MCP tool call failed")
        return self._extract_mcp_response_content(response)

    async def call_mcp_tool_async(
        self,
        target_binding: str,
        tool_name: str,
        arguments: Any,
        *,
        workflow_context: Optional[Dict[str, Any]] = None,
        source_agent: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        action_context: Optional[Dict[str, Any]] = None,
        poll_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 0.2,
    ) -> str:
        headers = self._apply_workflow_headers(
            {},
            workflow_context,
        )
        source_agent_id = self._resolve_source_agent_id(source_agent)
        tool_args = self._coerce_tool_arguments(arguments)
        response = await self.operations.mcp_communicate_async(
            self._request_gateway_async,
            headers,
            source_agent_id,
            target_binding,
            tool_name,
            tool_args,
            tool_call_id=tool_call_id,
            action_context=dict(action_context or {}),
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            telemetry_emitter=self.telemetry_emitter,
            telemetry_context=self.telemetry_context,
        )
        if isinstance(response, dict) and not response.get("success", True):
            raise RuntimeError(response.get("error") or "MCP tool call failed")
        return self._extract_mcp_response_content(response)

    async def call_mcp_tool_result_async(
        self,
        target_binding: str,
        tool_name: str,
        arguments: Any,
        *,
        workflow_context: Optional[Dict[str, Any]] = None,
        source_agent: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        action_context: Optional[Dict[str, Any]] = None,
        poll_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 0.2,
    ) -> Dict[str, Any]:
        """Invoke a tool and return its native MCP ``tools/call`` result."""
        headers = self._apply_workflow_headers(
            {},
            workflow_context,
        )
        response = await self.operations.mcp_communicate_async(
            self._request_gateway_async,
            headers,
            self._resolve_source_agent_id(source_agent),
            target_binding,
            tool_name,
            self._coerce_tool_arguments(arguments),
            tool_call_id=tool_call_id,
            action_context=dict(action_context or {}),
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            telemetry_emitter=self.telemetry_emitter,
            telemetry_context=self.telemetry_context,
        )
        if isinstance(response, dict) and not response.get("success", True):
            raise RuntimeError(response.get("error") or "MCP tool call failed")
        return _mcp_tool_result(response)


__all__ = ["MCPToolsClientMixin"]
