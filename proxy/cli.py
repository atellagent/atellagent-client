# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Small executable entrypoints for the optional MCP compatibility proxies."""

from __future__ import annotations

import argparse
import asyncio
import json
from secrets import token_urlsafe
import sys
from typing import Any, Mapping


from atellagent_client.integrations.agents.hook_control_protocol import (
    HookControlClient,
)

from .agent import MCPAgentProxy
from .bridge_config import load_local_mcp_bridge_config, local_mcp_bridge_config
from .contracts import (
    MCPToolResult,
    MCPVisibleTool,
)
from .tool import MCPToolProxy, MCPToolTarget


_MCP_RPC_TIMEOUT_SECONDS = 30.0




class _LocalMCPToolGateway:
    """Use the enrolled local control runtime without owning credentials."""

    def __init__(self, *, control_socket: str) -> None:
        # A conversational MCP client has no durable continuation for an
        # indefinitely pending call. Bound the bridge to the same end-to-end
        # control budget as the local runtime rather than polling for minutes.
        self._control = HookControlClient(
            control_socket,
            timeout_seconds=_MCP_RPC_TIMEOUT_SECONDS,
        )

    async def list_tools(self) -> tuple[MCPVisibleTool, ...]:
        result = await self._control.call("mcp.list", {})
        raw_tools = result.get("tools")
        if not isinstance(raw_tools, list):
            raise RuntimeError("local MCP control returned an invalid tool catalog")
        tools: list[MCPVisibleTool] = []
        names: set[str] = set()
        for raw_tool in raw_tools:
            if not isinstance(raw_tool, Mapping):
                raise RuntimeError("local MCP control returned an invalid tool catalog")
            tool = MCPVisibleTool(
                name=raw_tool.get("name", ""),
                description=raw_tool.get("description", ""),
                input_schema=raw_tool.get("inputSchema", {}),
            )
            if tool.name in names:
                raise RuntimeError("local MCP control returned an invalid tool catalog")
            names.add(tool.name)
            tools.append(tool)
        return tuple(tools)

    async def invoke_tool(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        peer_call_id: str | int | None,
    ) -> MCPToolResult:
        if not isinstance(arguments, Mapping):
            raise ValueError("MCP tool arguments must be an object")
        result = await self._control.call(
            "mcp.invoke",
            {
                "tool_name": str(tool_name),
                "arguments": dict(arguments),
                "tool_call_id": f"mcp-{token_urlsafe(24)}",
            },
        )
        content = result.get("content")
        if not isinstance(content, list) or not all(isinstance(item, Mapping) for item in content):
            raise RuntimeError("local MCP control returned an invalid tool result")
        return MCPToolResult(
            content=tuple(dict(item) for item in content),
            is_error=result.get("is_error") is True,
        )


async def _run_local_mcp_bridge(config_path: str | None = None, *, control_socket: str | None = None) -> int:
    config = (load_local_mcp_bridge_config(config_path) if config_path is not None
        else local_mcp_bridge_config(control_socket))
    proxy = MCPAgentProxy(
        gateway=_LocalMCPToolGateway(control_socket=config.control_socket)
    )
    for line in sys.stdin:
        if not line.strip():
            continue
        response = await proxy.handle_json_line(line)
        if response is not None:
            print(response, flush=True)
    return 0




def mcp_bridge_main(arguments=None) -> int:
    """Named entry point for an agent-facing, policy-bound MCP bridge."""
    parser = argparse.ArgumentParser(description="Run a local Atellagent MCP bridge over stdio.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", help="Path to the local MCP bridge YAML configuration.")
    source.add_argument("--socket", help="Selected enrolled controller socket or named pipe.")
    args = parser.parse_args(arguments)
    return asyncio.run(_run_local_mcp_bridge(args.config, control_socket=args.socket))


async def _run_tool_proxy(args: argparse.Namespace) -> int:
    try:
        arguments = json.loads(args.arguments)
    except json.JSONDecodeError as error:
        raise ValueError("--arguments must be a JSON object") from error
    if not isinstance(arguments, Mapping):
        raise ValueError("--arguments must be a JSON object")
    headers: dict[str, str] = {}
    for value in args.header:
        name, separator, header_value = value.partition(":")
        if not separator or not name.strip():
            raise ValueError("--header must use NAME:VALUE")
        headers[name.strip()] = header_value.strip()
    proxy = MCPToolProxy(target=MCPToolTarget(endpoint_url=args.target, credential_headers=headers or None))
    result = await proxy.call_tool(tool_name=args.tool, arguments=arguments, action_key=args.action_key)
    print(json.dumps(result, separators=(",", ":")), flush=True)
    return 0


def tool_main() -> int:
    parser = argparse.ArgumentParser(description="Call a customer-owned MCP target through the Atellagent compatibility proxy.")
    parser.add_argument("--target", required=True, help="Configured MCP target URL.")
    parser.add_argument("--tool", required=True, help="MCP tool name.")
    parser.add_argument("--arguments", default="{}", help="JSON object of tool arguments.")
    parser.add_argument("--action-key", required=True, help="Already-issued action idempotency key.")
    parser.add_argument("--header", action="append", default=[], help="Credential header in NAME:VALUE form; repeat as needed.")
    args = parser.parse_args()
    return asyncio.run(_run_tool_proxy(args))


__all__ = ["mcp_bridge_main", "tool_main"]
