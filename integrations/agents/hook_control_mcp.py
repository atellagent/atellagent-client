# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""MCP catalog and invocation handling for the local hook controller."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping

from atellagent_client.proxy.contracts import MCPVisibleTool
from atellagent_client.sdk.client_modules.mcp_tools import _mcp_tool_result
from atellagent_client.sdk.errors import PolicyViolationError

from .hook_control_helpers import _identifier, _object
from .hook_control_protocol import HookControlError


async def mcp_catalog(runtime) -> tuple[MCPVisibleTool, ...]:
    payload = await runtime.governance.mcp_catalog_async()
    raw_tools = payload.get("tools")
    if not isinstance(raw_tools, list):
        raise HookControlError("mcp_catalog_invalid")
    tools: list[MCPVisibleTool] = []
    names: set[str] = set()
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, Mapping):
            raise HookControlError("mcp_catalog_invalid")
        tool = MCPVisibleTool(
            name=raw_tool.get("name", ""),
            description=raw_tool.get("description", ""),
            input_schema=raw_tool.get("inputSchema", {}),
        )
        if tool.name in names:
            raise HookControlError("mcp_catalog_invalid")
        names.add(tool.name)
        tools.append(tool)
    return tuple(tools)

async def list_mcp_tools(runtime, raw_params: Any) -> Dict[str, Any]:
    if raw_params not in ({}, None):
        raise HookControlError("invalid_mcp_list_params")
    return {
        "tools": [tool.as_mcp_tool() for tool in await runtime._mcp_catalog()],
    }

async def invoke_mcp(runtime, raw_params: Any) -> Dict[str, Any]:
    params = _object(
        raw_params,
        "params",
        allowed={"tool_name", "arguments", "tool_call_id"},
    )
    tool_name = _identifier(params.get("tool_name"), "tool_name")
    arguments = params.get("arguments")
    if not isinstance(arguments, Mapping):
        raise HookControlError("invalid_arguments")
    tool_call_id = _identifier(params.get("tool_call_id"), "tool_call_id")
    operations = {"atellagent_list_servers", "atellagent_list_tools",
                  "atellagent_describe_tool", "atellagent_call_tool"}
    if tool_name not in operations:
        raise HookControlError("mcp_tool_not_assigned")
    try:
        result = await runtime.governance.mcp_catalog_async(
            operation=tool_name.removeprefix("atellagent_"), arguments=dict(arguments),
        )
        if tool_name != "atellagent_call_tool":
            return {"content": [{"type": "text", "text": json.dumps(
                {key: value for key, value in result.items() if key != "success"},
                separators=(",", ":"),
            )}], "is_error": False}
        action_key = await runtime._grant_scoped_action_key(f"mcp:{tool_call_id}")
        response = await runtime.governance.mcp_communicate_async(
            target_binding=result["target_binding"],
            tool_name=result["target_tool_name"],
            arguments=dict(arguments["arguments"]),
            tool_call_id=tool_call_id,
            action_key=action_key,
        )
    except PolicyViolationError as exc:
        # The Gateway is authoritative for signed policy outcomes. Both
        # admission denials and withheld provider responses are valid MCP
        # tool results, not bridge or transport failures. Keep messages
        # stable and do not reflect detector or policy detail.
        details = exc.details if isinstance(exc.details, Mapping) else {}
        message = (
            "Atellagent withheld this tool response under the configured policy."
            if details.get("response_delivery_status") == "withheld"
            else "Connect your provider account in Atellagent before using this tool."
            if exc.violation_type == "connection_required"
            else "Atellagent blocked this tool call under the configured policy."
        )
        return {
            "content": [{"type": "text", "text": message}],
            "is_error": True,
        }
    result = _mcp_tool_result(response)
    return {
        "content": result["content"],
        "is_error": result.get("isError") is True,
    }
