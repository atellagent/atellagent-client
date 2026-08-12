# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""SDK MCP tool invocation client methods."""

from __future__ import annotations

from typing import Any, Dict, Optional


class MCPToolsClientMixin:
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
        client = self._ensure_authenticated_sync()
        headers = self._apply_workflow_headers(
            self.auth_manager.get_auth_headers(),
            workflow_context,
        )
        source_agent_id = self._resolve_source_agent_id(source_agent)
        tool_args = self._coerce_tool_arguments(arguments)
        response = self.operations.mcp_communicate_sync(
            client,
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
        session = await self._ensure_authenticated_async()
        headers = self._apply_workflow_headers(
            self.auth_manager.get_auth_headers(),
            workflow_context,
        )
        source_agent_id = self._resolve_source_agent_id(source_agent)
        tool_args = self._coerce_tool_arguments(arguments)
        response = await self.operations.mcp_communicate_async(
            session,
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


__all__ = ["MCPToolsClientMixin"]
