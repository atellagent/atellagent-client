# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Small executable entrypoints for the optional MCP compatibility proxies."""

from __future__ import annotations

import argparse
import asyncio
import json
from secrets import token_urlsafe
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

from atellagent_client.integrations.agents.hook_control_protocol import (
    HookControlClient,
)
from atellagent_client.sdk.client_modules.client_class import AtellagentClient
from atellagent_client.sdk.config import load_service_account_config_from_yaml

from .agent import MCPAgentProxy, MCPAgentProxyError
from .bridge_config import load_local_mcp_bridge_config
from .contracts import (
    MCPProxyTool,
    MCPToolResult,
    MCPVisibleTool,
    _ConfiguredMCPToolGateway,
)
from .tool import MCPToolProxy, MCPToolTarget


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _read_agent_proxy_config(path: str) -> tuple[str, str | None, list[MCPProxyTool]]:
    document = _object(
        yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {},
        "agent proxy configuration",
    )
    client_config = str(document.get("client_config") or "").strip()
    if not client_config:
        raise ValueError("client_config is required")
    client_config_path = Path(client_config)
    if not client_config_path.is_absolute():
        client_config_path = Path(path).resolve().parent / client_config_path
    raw_tools = document.get("tools")
    if not isinstance(raw_tools, list) or not raw_tools:
        raise ValueError("tools must be a non-empty list")
    tools = [
        MCPProxyTool(
            name=_object(value, "tool").get("name", ""),
            description=_object(value, "tool").get("description", ""),
            input_schema=_object(value, "tool").get("input_schema", {}),
            target_binding=_object(value, "tool").get("target_binding", ""),
            target_tool_name=_object(value, "tool").get("target_tool_name", ""),
        )
        for value in raw_tools
    ]
    source_agent = str(document.get("source_agent") or "").strip() or None
    return str(client_config_path), source_agent, tools


async def _run_agent_proxy(config_path: str) -> int:
    client_config_path, source_agent, tools = _read_agent_proxy_config(config_path)
    client = AtellagentClient(load_service_account_config_from_yaml(client_config_path))
    proxy = MCPAgentProxy(
        gateway=_ConfiguredMCPToolGateway(
            client=client,
            tools=tools,
            source_agent=source_agent,
        )
    )
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                response = await proxy.handle_json_line(line)
            except MCPAgentProxyError as error:
                response = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": str(error)}})
            if response is not None:
                print(response, flush=True)
    finally:
        await client.close_async()
    return 0


class _LocalMCPToolGateway:
    """Use the enrolled local control runtime without owning credentials."""

    def __init__(self, *, control_socket: str) -> None:
        self._control = HookControlClient(control_socket, timeout_seconds=305.0)

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


async def _run_local_mcp_bridge(config_path: str) -> int:
    config = load_local_mcp_bridge_config(config_path)
    proxy = MCPAgentProxy(
        gateway=_LocalMCPToolGateway(control_socket=config.control_socket)
    )
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            response = await proxy.handle_json_line(line)
        except MCPAgentProxyError as error:
            response = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": str(error)}})
        if response is not None:
            print(response, flush=True)
    return 0


def agent_main() -> int:
    parser = argparse.ArgumentParser(description="Run an Atellagent local MCP bridge over stdio.")
    parser.add_argument("--config", required=True, help="Path to the customer-owned agent proxy YAML configuration.")
    args = parser.parse_args()
    return asyncio.run(_run_agent_proxy(args.config))


def mcp_bridge_main() -> int:
    """Named entry point for an agent-facing, policy-bound MCP bridge."""
    parser = argparse.ArgumentParser(description="Run a local Atellagent MCP bridge over stdio.")
    parser.add_argument("--config", required=True, help="Path to the local MCP bridge YAML configuration.")
    args = parser.parse_args()
    return asyncio.run(_run_local_mcp_bridge(args.config))


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


__all__ = ["agent_main", "tool_main"]
