# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""MCP server facade for the customer-operated agent proxy offering."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from secrets import token_urlsafe
from time import monotonic
from typing import Any, Mapping

from .contracts import _ProxyInvocationPort
from .tool import LEGACY_MCP_PROTOCOL_VERSIONS, MODERN_MCP_PROTOCOL_VERSION, MCPPeerProtocol


_SESSION_HEADER = "mcp-session-id"
_PROTOCOL_HEADER = "mcp-protocol-version"
_PROTOCOL_META_KEY = "io.modelcontextprotocol/protocolVersion"
logger = logging.getLogger(__name__)


class MCPAgentProxyError(ValueError):
    """A peer request cannot be safely interpreted by the agent proxy."""


@dataclass(frozen=True)
class ProxyResponse:
    """One HTTP-agnostic MCP response rendered by the agent proxy."""

    document: Mapping[str, Any] | None
    protocol: MCPPeerProtocol
    session_id: str | None = None


@dataclass(frozen=True)
class _LegacySession:
    protocol_version: str
    expires_at: float


class _LegacySessions:
    """Bounded local negotiation state; never action or policy state."""

    def __init__(self, *, ttl_seconds: int, max_sessions: int) -> None:
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._max_sessions = max(1, int(max_sessions))
        self._sessions: dict[str, _LegacySession] = {}

    def create(self, protocol_version: str) -> str:
        self._discard_expired()
        if len(self._sessions) >= self._max_sessions:
            raise MCPAgentProxyError("legacy MCP session capacity is exhausted")
        session_id = token_urlsafe(32)
        self._sessions[session_id] = _LegacySession(
            protocol_version=protocol_version,
            expires_at=monotonic() + self._ttl_seconds,
        )
        return session_id

    def get(self, session_id: str | None) -> _LegacySession:
        self._discard_expired()
        session = self._sessions.get(str(session_id or ""))
        if session is None:
            raise MCPAgentProxyError("MCP legacy session is unknown or expired")
        return session

    def close(self, session_id: str | None) -> None:
        self._sessions.pop(str(session_id or ""), None)

    def _discard_expired(self) -> None:
        now = monotonic()
        for session_id, session in tuple(self._sessions.items()):
            if session.expires_at <= now:
                self._sessions.pop(session_id, None)


def _request_id(document: Mapping[str, Any]) -> str | int | None:
    value = document.get("id")
    return value if isinstance(value, (str, int)) and not isinstance(value, bool) else None


def _error(document: Mapping[str, Any], message: str, *, code: int = -32600) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": _request_id(document), "error": {"code": code, "message": message}}


def _result(document: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": _request_id(document), "result": dict(result)}


def _header(headers: Mapping[str, str] | None, name: str) -> str | None:
    if headers is None:
        return None
    value = next((value for key, value in headers.items() if key.lower() == name), None)
    text = str(value or "").strip()
    return text or None


def _requested_protocol(document: Mapping[str, Any], headers: Mapping[str, str] | None) -> MCPPeerProtocol:
    header = _header(headers, _PROTOCOL_HEADER)
    method = str(document.get("method") or "")
    params = document.get("params") if isinstance(document.get("params"), Mapping) else {}
    if method == "initialize":
        declared = str(params.get("protocolVersion") or "").strip()
        if declared not in LEGACY_MCP_PROTOCOL_VERSIONS:
            raise MCPAgentProxyError("legacy initialize request has an unsupported protocol version")
        if header and header != declared:
            raise MCPAgentProxyError("MCP protocol header and initialize request disagree")
        return MCPPeerProtocol.LEGACY_STREAMABLE_HTTP
    if method == "server/discover":
        meta = params.get("_meta") if isinstance(params.get("_meta"), Mapping) else {}
        declared = str(meta.get(_PROTOCOL_META_KEY) or header or "").strip()
        if declared != MODERN_MCP_PROTOCOL_VERSION:
            raise MCPAgentProxyError("modern discovery request has an unsupported protocol version")
        return MCPPeerProtocol.MODERN
    if header == MODERN_MCP_PROTOCOL_VERSION:
        return MCPPeerProtocol.MODERN
    if header in LEGACY_MCP_PROTOCOL_VERSIONS or _header(headers, _SESSION_HEADER):
        return MCPPeerProtocol.LEGACY_STREAMABLE_HTTP
    raise MCPAgentProxyError("MCP peer did not provide a valid protocol negotiation")


class MCPAgentProxy:
    """Translate inbound MCP requests into the configured invocation port.

    This object is transport-neutral.  It can be used by the supplied stdio
    executable or mounted by a customer-owned local HTTP server without
    exposing platform implementation details.
    """

    def __init__(
        self,
        *,
        gateway: _ProxyInvocationPort,
        legacy_session_ttl_seconds: int = 900,
        legacy_max_sessions: int = 1024,
    ) -> None:
        self._gateway = gateway
        self._legacy_sessions = _LegacySessions(
            ttl_seconds=legacy_session_ttl_seconds,
            max_sessions=legacy_max_sessions,
        )
        self._stdio_legacy_session_id: str | None = None

    async def handle(
        self,
        document: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> ProxyResponse:
        if document.get("jsonrpc") != "2.0" or not isinstance(document.get("method"), str):
            raise MCPAgentProxyError("MCP request must be a JSON-RPC method object")
        protocol = _requested_protocol(document, headers)
        method = str(document["method"])
        session_id = _header(headers, _SESSION_HEADER)
        if protocol is MCPPeerProtocol.LEGACY_STREAMABLE_HTTP and method != "initialize":
            session = self._legacy_sessions.get(session_id)
            header_version = _header(headers, _PROTOCOL_HEADER)
            if header_version and header_version != session.protocol_version:
                raise MCPAgentProxyError("MCP legacy session protocol changed")

        if method == "initialize":
            params = document.get("params") if isinstance(document.get("params"), Mapping) else {}
            version = str(params.get("protocolVersion") or "")
            created = self._legacy_sessions.create(version)
            logger.info("MCP compatibility peer negotiated legacy revision %s", version)
            return ProxyResponse(
                protocol=protocol,
                session_id=created,
                document=_result(
                    document,
                    {
                        "protocolVersion": version,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "atellagent-agent-proxy", "version": "1"},
                    },
                ),
            )
        if method == "server/discover":
            logger.info("MCP compatibility peer negotiated modern revision %s", MODERN_MCP_PROTOCOL_VERSION)
            return ProxyResponse(
                protocol=protocol,
                document=_result(
                    document,
                    {
                        "resultType": "complete",
                        "supportedVersions": [MODERN_MCP_PROTOCOL_VERSION],
                        "cacheScope": "private",
                        "ttlMs": 0,
                        "capabilities": {"tools": True},
                    },
                ),
            )
        if method == "notifications/initialized":
            return ProxyResponse(protocol=protocol, document=None)
        if method == "tools/list":
            tools = await self._gateway.list_tools()
            return ProxyResponse(
                protocol=protocol,
                session_id=session_id if protocol is MCPPeerProtocol.LEGACY_STREAMABLE_HTTP else None,
                document=_result(document, {"tools": [tool.as_mcp_tool() for tool in tools]}),
            )
        if method == "tools/call":
            params = document.get("params")
            if not isinstance(params, Mapping):
                return ProxyResponse(protocol=protocol, document=_error(document, "tools/call params must be an object"))
            name = str(params.get("name") or "").strip()
            arguments = params.get("arguments", {})
            if not name or not isinstance(arguments, Mapping):
                return ProxyResponse(protocol=protocol, document=_error(document, "tools/call requires name and object arguments"))
            try:
                result = await self._gateway.invoke_tool(
                    tool_name=name,
                    arguments=dict(arguments),
                    peer_call_id=_request_id(document),
                )
            except Exception as error:
                return ProxyResponse(protocol=protocol, document=_error(document, str(error), code=-32000))
            return ProxyResponse(
                protocol=protocol,
                session_id=session_id if protocol is MCPPeerProtocol.LEGACY_STREAMABLE_HTTP else None,
                document=_result(document, result.as_mcp_result()),
            )
        return ProxyResponse(protocol=protocol, document=_error(document, "MCP method is unsupported", code=-32601))

    async def handle_json_line(self, line: str) -> str | None:
        """Handle one stdio JSON-RPC line for the bundled agent-proxy CLI."""
        try:
            document = json.loads(line)
        except json.JSONDecodeError as error:
            raise MCPAgentProxyError("MCP stdio input is not JSON") from error
        if not isinstance(document, Mapping):
            raise MCPAgentProxyError("MCP stdio input must be an object")
        headers = (
            {_SESSION_HEADER: self._stdio_legacy_session_id}
            if self._stdio_legacy_session_id
            else None
        )
        response = await self.handle(document, headers=headers)
        if response.protocol is MCPPeerProtocol.LEGACY_STREAMABLE_HTTP and response.session_id:
            self._stdio_legacy_session_id = response.session_id
        return json.dumps(response.document, separators=(",", ":")) if response.document is not None else None


__all__ = ["MCPAgentProxy", "MCPAgentProxyError", "ProxyResponse"]
