# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public, credential-free contracts for the optional MCP proxy offerings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


def _required_text(value: object, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


@dataclass(frozen=True)
class MCPProxyTool:
    """One MCP-visible tool bound to a configured client target."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    target_binding: str
    target_tool_name: str

    def __post_init__(self) -> None:
        name = _required_text(self.name, field_name="name")
        schema = dict(self.input_schema or {})
        if schema.get("type") != "object":
            raise ValueError("input_schema.type must be 'object'")
        if not isinstance(schema.get("properties", {}), Mapping):
            raise ValueError("input_schema.properties must be an object")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", _required_text(self.description, field_name="description"))
        object.__setattr__(self, "input_schema", schema)
        object.__setattr__(
            self,
            "target_binding",
            _required_text(self.target_binding, field_name="target_binding"),
        )
        object.__setattr__(
            self,
            "target_tool_name",
            _required_text(self.target_tool_name, field_name="target_tool_name"),
        )

    def as_mcp_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
        }


@dataclass(frozen=True)
class MCPToolResult:
    """A tool result safe to render on an MCP peer connection."""

    content: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    is_error: bool = False

    def as_mcp_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {"content": [dict(item) for item in self.content]}
        if self.is_error:
            result["isError"] = True
        return result


class _ProxyInvocationPort(Protocol):
    """Configured tool catalog and invocation port for an MCP proxy."""

    async def list_tools(self) -> Sequence[MCPProxyTool]: ...

    async def invoke_tool(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        peer_call_id: str | int | None,
    ) -> MCPToolResult: ...


class _ConfiguredMCPToolGateway:
    """Map configured MCP-visible tools to the client invocation surface."""

    def __init__(
        self,
        *,
        client: Any,
        tools: Sequence[MCPProxyTool],
        source_agent: str | None = None,
    ) -> None:
        catalog: dict[str, MCPProxyTool] = {}
        for tool in tools:
            if not isinstance(tool, MCPProxyTool):
                raise TypeError("tools must contain MCPProxyTool values")
            if tool.name in catalog:
                raise ValueError(f"duplicate MCP proxy tool: {tool.name}")
            catalog[tool.name] = tool
        if not catalog:
            raise ValueError("at least one MCP proxy tool is required")
        self._client = client
        self._tools = catalog
        self._source_agent = str(source_agent or "").strip() or None

    async def list_tools(self) -> Sequence[MCPProxyTool]:
        return tuple(self._tools.values())

    async def invoke_tool(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        peer_call_id: str | int | None,
    ) -> MCPToolResult:
        tool = self._tools.get(str(tool_name or "").strip())
        if tool is None:
            raise ValueError("MCP proxy tool is not configured")
        if not isinstance(arguments, Mapping):
            raise ValueError("MCP tool arguments must be an object")
        call = getattr(self._client, "call_mcp_tool_async", None)
        if call is None:
            raise TypeError("client must provide call_mcp_tool_async")
        content = await call(
            tool.target_binding,
            tool.target_tool_name,
            dict(arguments),
            source_agent=self._source_agent,
            tool_call_id=str(peer_call_id) if peer_call_id is not None else None,
        )
        return MCPToolResult(content=({"type": "text", "text": str(content)},))


__all__ = [
    "MCPProxyTool",
    "MCPToolResult",
]
