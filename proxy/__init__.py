# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Optional MCP compatibility proxies for customer-operated agent and tool peers."""

from .agent import MCPAgentProxy, ProxyResponse
from .contracts import MCPProxyTool, MCPToolResult
from .tool import MCPPeerProtocol, MCPToolProxy, MCPToolTarget

__all__ = [
    "MCPAgentProxy",
    "MCPPeerProtocol",
    "MCPProxyTool",
    "MCPToolProxy",
    "MCPToolResult",
    "MCPToolTarget",
    "ProxyResponse",
]
