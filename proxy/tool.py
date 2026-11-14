# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under LICENSE.md in this source tree.
"""Thin customer tool facade over the sole supported MCP target adapter."""

from enum import Enum
from typing import Mapping, Any
from uuid import uuid4

from atellagent_client.sdk.config import BridgeDeploymentConfig
from .client import MCPClient, MCP_PROTOCOL_VERSION
from .targets import MCPToolTarget

MODERN_MCP_PROTOCOL_VERSION = MCP_PROTOCOL_VERSION
SESSION_MCP_PROTOCOL_VERSIONS = frozenset({"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"})


class MCPPeerProtocol(str, Enum):
    MODERN = "modern"
    STATEFUL = "stateful"


class MCPToolProxyError(RuntimeError):
    """A configured target refused one admitted invocation."""


class MCPToolProxy:
    """Call a configured upstream once; target negotiation never chooses policy.

    The bridge retains loopback-only URLs. This facade retains its existing
    explicit HTTPS target support and uses the same adapter and wire parser.
    """

    def __init__(self, *, target: MCPToolTarget, timeout_seconds: float = 300):
        self._target = target
        self._adapter = MCPClient(BridgeDeploymentConfig(target_transport="http",
            target_url=target.endpoint_url, upstream_headers=dict(target.credential_headers or {})),
            loopback_only=False, timeout_seconds=timeout_seconds)

    async def selected_protocol(self):
        await self._adapter.manifest()
        return MCPPeerProtocol.MODERN

    async def call_tool(self, *, tool_name: str, arguments: Mapping[str, Any], action_key: str):
        if (not isinstance(tool_name, str) or not tool_name or tool_name != tool_name.strip()
                or not isinstance(arguments, Mapping) or not isinstance(action_key, str)
                or not 1 <= len(action_key) <= 255 or action_key != action_key.strip()):
            raise MCPToolProxyError("Tool name, arguments and accepted action key are required")
        try:
            response = await self._adapter.invoke({"jsonrpc":"2.0", "id":str(uuid4()),
                "method":"tools/call", "params":{"name":tool_name, "arguments":dict(arguments)}}, action_key)
            return response["result"]
        except Exception:
            raise MCPToolProxyError("MCP target did not return a complete tool result; do not automatically repeat the action") from None

    async def close(self):
        await self._adapter.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
