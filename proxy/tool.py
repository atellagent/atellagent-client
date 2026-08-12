# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""MCP client facade for the customer-operated tool proxy offering."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx


MODERN_MCP_PROTOCOL_VERSION = "2026-07-28"
LEGACY_MCP_PROTOCOL_VERSIONS = frozenset({"2024-11-05", "2025-03-26", "2025-06-18"})
_SAFE_MODERN_INCOMPATIBLE_STATUSES = frozenset({404, 405, 415, 501})
_MAX_PROTOCOL_BODY_BYTES = 64 * 1024
logger = logging.getLogger(__name__)


class MCPPeerProtocol(str, Enum):
    MODERN = "modern"
    LEGACY_STREAMABLE_HTTP = "legacy_streamable_http"
    LEGACY_HTTP_SSE = "legacy_http_sse"


class MCPToolProxyError(RuntimeError):
    """A customer-configured MCP target failed compatibility validation."""


class _SSEStreamInterrupted(Exception):
    """One legacy SSE stream ended after an event cursor was observed."""

    def __init__(self, *, event_id: str | None, received: int) -> None:
        self.event_id = event_id
        self.received = received


@dataclass(frozen=True)
class MCPToolTarget:
    """One local or private MCP target; the wire revision is intentionally absent."""

    endpoint_url: str
    credential_headers: Mapping[str, str] | None = None
    probe_cache_ttl_seconds: float = 300.0

    def __post_init__(self) -> None:
        endpoint = str(self.endpoint_url or "").strip()
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("endpoint_url must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("endpoint_url must not contain credentials or fragments")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("non-loopback MCP targets require HTTPS")
        headers = dict(self.credential_headers or {})
        forbidden = {
            "accept",
            "content-type",
            "idempotency-key",
            "mcp-protocol-version",
            "mcp-session-id",
        }
        if any(str(name).lower() in forbidden for name in headers):
            raise ValueError("credential_headers must not override MCP transport headers")
        if any(not isinstance(name, str) or not isinstance(value, str) for name, value in headers.items()):
            raise ValueError("credential_headers must contain string pairs")
        ttl = float(self.probe_cache_ttl_seconds)
        if ttl < 1 or ttl > 3600:
            raise ValueError("probe_cache_ttl_seconds must be between 1 and 3600")
        object.__setattr__(self, "endpoint_url", endpoint)
        object.__setattr__(self, "credential_headers", headers)
        object.__setattr__(self, "probe_cache_ttl_seconds", ttl)


@dataclass(frozen=True)
class _SelectedTarget:
    protocol: MCPPeerProtocol
    endpoint_url: str
    session_id: str | None = None
    message_endpoint_url: str | None = None


def _jsonrpc_request(*, request_id: str, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)}


def _modern_discovery_request() -> dict[str, Any]:
    return _jsonrpc_request(
        request_id="atellagent-mcp-proxy-discover",
        method="server/discover",
        params={
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MODERN_MCP_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "atellagent-tool-proxy", "version": "1"},
            }
        },
    )


def _legacy_initialize_request() -> dict[str, Any]:
    return _jsonrpc_request(
        request_id="atellagent-mcp-proxy-initialize",
        method="initialize",
        params={
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "atellagent-tool-proxy", "version": "1"},
        },
    )


def _response_document(response: httpx.Response, *, label: str) -> Mapping[str, Any]:
    if len(response.content) > _MAX_PROTOCOL_BODY_BYTES:
        raise MCPToolProxyError(f"{label} response exceeded its size limit")
    if "application/json" not in str(response.headers.get("content-type") or "").lower():
        raise MCPToolProxyError(f"{label} response must be JSON")
    try:
        document = response.json()
    except ValueError as error:
        raise MCPToolProxyError(f"{label} response is malformed") from error
    if not isinstance(document, Mapping):
        raise MCPToolProxyError(f"{label} response must be an object")
    return document


def _tool_result(document: Mapping[str, Any], *, request_id: str, modern: bool) -> Mapping[str, Any]:
    if document.get("jsonrpc") != "2.0" or document.get("id") != request_id:
        raise MCPToolProxyError("MCP tool response does not match the request")
    if "error" in document:
        raise MCPToolProxyError("MCP target returned a tool error")
    result = document.get("result")
    if not isinstance(result, Mapping) or not isinstance(result.get("content"), list):
        raise MCPToolProxyError("MCP tool response is malformed")
    if any(not isinstance(item, Mapping) for item in result["content"]):
        raise MCPToolProxyError("MCP tool response content is malformed")
    if modern and result.get("resultType") != "complete":
        raise MCPToolProxyError("modern MCP tool response is incomplete")
    return result


class MCPToolProxy:
    """Discover a target protocol at runtime and execute one admitted tool call.

    The selected route is strictly local, short-lived operational state. The
    caller supplies the already-issued action key, which is forwarded only as
    the target idempotency key and is never derived from an MCP request ID.
    """

    def __init__(self, *, target: MCPToolTarget, timeout_seconds: float = 300.0) -> None:
        self._target = target
        self._timeout_seconds = min(max(float(timeout_seconds), 1.0), 300.0)
        self._selected: tuple[_SelectedTarget, float] | None = None
        self._legacy_initialized_session_id: str | None = None

    async def selected_protocol(self) -> MCPPeerProtocol:
        return (await self._select()).protocol

    async def call_tool(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        action_key: str,
    ) -> Mapping[str, Any]:
        name = str(tool_name or "").strip()
        key = str(action_key or "").strip()
        if not name or not isinstance(arguments, Mapping):
            raise MCPToolProxyError("tool_name and object arguments are required")
        if not key or len(key) > 255:
            raise MCPToolProxyError("action_key is invalid")
        route = await self._select()
        if route.protocol is MCPPeerProtocol.MODERN:
            return await self._call_modern(route=route, tool_name=name, arguments=arguments, action_key=key)
        if route.protocol is MCPPeerProtocol.LEGACY_STREAMABLE_HTTP:
            return await self._call_legacy_streamable(route=route, tool_name=name, arguments=arguments, action_key=key)
        return await self._call_legacy_sse(route=route, tool_name=name, arguments=arguments, action_key=key)

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )

    def _headers(self, *, action_key: str | None = None, session_id: str | None = None) -> dict[str, str]:
        headers = {
            **dict(self._target.credential_headers or {}),
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if action_key:
            headers["Idempotency-Key"] = action_key
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        return headers

    async def _select(self) -> _SelectedTarget:
        cached = self._selected
        if cached is not None and cached[1] > monotonic():
            return cached[0]
        endpoint = self._target.endpoint_url
        try:
            async with await self._client() as client:
                modern = await client.post(
                    endpoint,
                    headers={
                        **self._headers(),
                        "MCP-Protocol-Version": MODERN_MCP_PROTOCOL_VERSION,
                        "Mcp-Method": "server/discover",
                    },
                    json=_modern_discovery_request(),
                )
                if modern.status_code == 200:
                    document = _response_document(modern, label="modern MCP discovery")
                    result = document.get("result")
                    if (
                        document.get("jsonrpc") == "2.0"
                        and isinstance(result, Mapping)
                        and result.get("resultType") == "complete"
                        and isinstance(result.get("supportedVersions"), list)
                        and MODERN_MCP_PROTOCOL_VERSION in result["supportedVersions"]
                    ):
                        logger.info("MCP compatibility target negotiated modern revision %s", MODERN_MCP_PROTOCOL_VERSION)
                        return self._cache(_SelectedTarget(MCPPeerProtocol.MODERN, endpoint))
                    raise MCPToolProxyError("modern MCP discovery response is invalid")
                if modern.status_code not in _SAFE_MODERN_INCOMPATIBLE_STATUSES:
                    raise MCPToolProxyError(f"modern MCP discovery failed with HTTP {modern.status_code}")
                legacy = await client.post(endpoint, headers=self._headers(), json=_legacy_initialize_request())
                if legacy.status_code == 200:
                    document = _response_document(legacy, label="legacy MCP initialization")
                    result = document.get("result")
                    if (
                        document.get("jsonrpc") == "2.0"
                        and isinstance(result, Mapping)
                        and result.get("protocolVersion") in LEGACY_MCP_PROTOCOL_VERSIONS
                    ):
                        session_id = str(legacy.headers.get("mcp-session-id") or "").strip() or None
                        logger.info("MCP compatibility target negotiated legacy revision %s", result.get("protocolVersion"))
                        return self._cache(_SelectedTarget(MCPPeerProtocol.LEGACY_STREAMABLE_HTTP, endpoint, session_id=session_id))
                    raise MCPToolProxyError("legacy MCP initialization response is invalid")
                if legacy.status_code not in _SAFE_MODERN_INCOMPATIBLE_STATUSES:
                    raise MCPToolProxyError(f"legacy MCP initialization failed with HTTP {legacy.status_code}")
                message_endpoint = await self._discover_sse_endpoint(client=client, endpoint_url=endpoint)
                logger.info("MCP compatibility target negotiated legacy HTTP+SSE")
                return self._cache(_SelectedTarget(MCPPeerProtocol.LEGACY_HTTP_SSE, endpoint, message_endpoint_url=message_endpoint))
        except httpx.TimeoutException as error:
            raise MCPToolProxyError("MCP protocol selection timed out") from error
        except httpx.TransportError as error:
            raise MCPToolProxyError("MCP protocol selection transport failed") from error

    def _cache(self, route: _SelectedTarget) -> _SelectedTarget:
        if route.session_id != self._legacy_initialized_session_id:
            self._legacy_initialized_session_id = None
        self._selected = (route, monotonic() + self._target.probe_cache_ttl_seconds)
        return route

    async def _call_modern(self, *, route: _SelectedTarget, tool_name: str, arguments: Mapping[str, Any], action_key: str) -> Mapping[str, Any]:
        request_id = str(uuid4())
        document = _jsonrpc_request(
            request_id=request_id,
            method="tools/call",
            params={
                "name": tool_name,
                "arguments": dict(arguments),
                "_meta": {"io.modelcontextprotocol/protocolVersion": MODERN_MCP_PROTOCOL_VERSION},
            },
        )
        async with await self._client() as client:
            response = await client.post(
                route.endpoint_url,
                headers={**self._headers(action_key=action_key), "MCP-Protocol-Version": MODERN_MCP_PROTOCOL_VERSION, "Mcp-Method": "tools/call", "Mcp-Name": tool_name},
                json=document,
            )
        if response.status_code != 200:
            raise MCPToolProxyError(f"modern MCP tool call failed with HTTP {response.status_code}")
        if response.headers.get("mcp-session-id"):
            raise MCPToolProxyError("modern MCP target returned forbidden session state")
        return _tool_result(_response_document(response, label="modern MCP tool"), request_id=request_id, modern=True)

    async def _call_legacy_streamable(self, *, route: _SelectedTarget, tool_name: str, arguments: Mapping[str, Any], action_key: str) -> Mapping[str, Any]:
        async with await self._client() as client:
            if route.session_id and route.session_id != self._legacy_initialized_session_id:
                initialized = await client.post(
                    route.endpoint_url,
                    headers=self._headers(action_key=action_key, session_id=route.session_id),
                    json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                )
                if initialized.status_code not in {200, 202}:
                    raise MCPToolProxyError("legacy MCP initialized notification failed")
                self._legacy_initialized_session_id = route.session_id
            request_id = str(uuid4())
            response = await client.post(
                route.endpoint_url,
                headers=self._headers(action_key=action_key, session_id=route.session_id),
                json=_jsonrpc_request(request_id=request_id, method="tools/call", params={"name": tool_name, "arguments": dict(arguments)}),
            )
        if response.status_code == 404 and route.session_id:
            self._selected = None
            self._legacy_initialized_session_id = None
            raise MCPToolProxyError("legacy MCP session expired; reconnect and retry the invocation")
        if response.status_code != 200:
            raise MCPToolProxyError(f"legacy MCP tool call failed with HTTP {response.status_code}")
        return _tool_result(_response_document(response, label="legacy MCP tool"), request_id=request_id, modern=False)

    async def _discover_sse_endpoint(self, *, client: httpx.AsyncClient, endpoint_url: str) -> str:
        async with client.stream("GET", endpoint_url, headers={**dict(self._target.credential_headers or {}), "Accept": "text/event-stream"}) as response:
            if response.status_code != 200 or "text/event-stream" not in str(response.headers.get("content-type") or "").lower():
                raise MCPToolProxyError("legacy MCP target did not open an SSE stream")
            event_name: str | None = None
            received = 0
            async for line in response.aiter_lines():
                received += len(line.encode("utf-8")) + 1
                if received > _MAX_PROTOCOL_BODY_BYTES:
                    raise MCPToolProxyError("legacy MCP SSE discovery exceeded its size limit")
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif event_name == "endpoint" and line.startswith("data:"):
                    return self._same_origin_endpoint(endpoint_url, line[5:].strip())
        raise MCPToolProxyError("legacy MCP SSE stream ended before its endpoint")

    @staticmethod
    def _same_origin_endpoint(origin_url: str, candidate: str) -> str:
        endpoint = urljoin(origin_url, candidate)
        origin = urlsplit(origin_url)
        destination = urlsplit(endpoint)
        if not candidate or destination.scheme != origin.scheme or destination.hostname != origin.hostname or destination.port != origin.port:
            raise MCPToolProxyError("legacy MCP SSE endpoint is outside the configured target")
        return endpoint

    async def _call_legacy_sse(self, *, route: _SelectedTarget, tool_name: str, arguments: Mapping[str, Any], action_key: str) -> Mapping[str, Any]:
        if not route.message_endpoint_url:
            raise MCPToolProxyError("legacy MCP SSE route is incomplete")
        request_id = str(uuid4())
        async with await self._client() as client:
            async with client.stream("GET", route.endpoint_url, headers={**dict(self._target.credential_headers or {}), "Accept": "text/event-stream"}) as response:
                if response.status_code != 200 or "text/event-stream" not in str(response.headers.get("content-type") or "").lower():
                    raise MCPToolProxyError("legacy MCP SSE stream failed")
                await self._post_sse_message(client, route.message_endpoint_url, _legacy_initialize_request(), action_key)
                await self._post_sse_message(client, route.message_endpoint_url, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, action_key)
                await self._post_sse_message(client, route.message_endpoint_url, _jsonrpc_request(request_id=request_id, method="tools/call", params={"name": tool_name, "arguments": dict(arguments)}), action_key)
                try:
                    document = await self._read_sse_result(response=response, request_id=request_id)
                except _SSEStreamInterrupted as interrupted:
                    document = await self._resume_sse_result(
                        client=client,
                        endpoint_url=route.endpoint_url,
                        request_id=request_id,
                        event_id=interrupted.event_id,
                        received=interrupted.received,
                    )
        return _tool_result(document, request_id=request_id, modern=False)

    async def _post_sse_message(self, client: httpx.AsyncClient, endpoint_url: str, document: Mapping[str, Any], action_key: str) -> None:
        response = await client.post(endpoint_url, headers=self._headers(action_key=action_key), json=dict(document))
        if response.status_code not in {200, 202}:
            raise MCPToolProxyError(f"legacy MCP SSE message failed with HTTP {response.status_code}")

    async def _resume_sse_result(
        self,
        *,
        client: httpx.AsyncClient,
        endpoint_url: str,
        request_id: str,
        event_id: str | None,
        received: int,
    ) -> Mapping[str, Any]:
        if not event_id:
            raise MCPToolProxyError("legacy MCP SSE stream ended without a resumable event cursor")
        headers = {
            **dict(self._target.credential_headers or {}),
            "Accept": "text/event-stream",
            "Last-Event-ID": event_id,
        }
        async with client.stream("GET", endpoint_url, headers=headers) as response:
            if response.status_code != 200 or "text/event-stream" not in str(response.headers.get("content-type") or "").lower():
                raise MCPToolProxyError("legacy MCP SSE resumption failed")
            try:
                return await self._read_sse_result(response=response, request_id=request_id, received=received)
            except _SSEStreamInterrupted as interrupted:
                if interrupted.event_id:
                    raise MCPToolProxyError("legacy MCP SSE stream ended before response delivery") from None
                raise MCPToolProxyError("legacy MCP SSE stream ended without a resumable event cursor") from None

    async def _read_sse_result(
        self,
        *,
        response: httpx.Response,
        request_id: str,
        received: int = 0,
    ) -> Mapping[str, Any]:
        event_id: str | None = None
        data_lines: list[str] = []
        try:
            async for line in response.aiter_lines():
                received += len(line.encode("utf-8")) + 1
                if received > _MAX_PROTOCOL_BODY_BYTES:
                    raise MCPToolProxyError("legacy MCP SSE response exceeded its size limit")
                if line.startswith("id:"):
                    event_id = line[3:].strip() or event_id
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif not line and data_lines:
                    try:
                        document = json.loads("\n".join(data_lines))
                    except json.JSONDecodeError as error:
                        raise MCPToolProxyError("legacy MCP SSE response is malformed") from error
                    if isinstance(document, Mapping) and document.get("id") == request_id:
                        return document
                    data_lines = []
        except httpx.TransportError:
            pass
        raise _SSEStreamInterrupted(event_id=event_id, received=received)


__all__ = [
    "LEGACY_MCP_PROTOCOL_VERSIONS",
    "MODERN_MCP_PROTOCOL_VERSION",
    "MCPPeerProtocol",
    "MCPToolProxy",
    "MCPToolProxyError",
    "MCPToolTarget",
]
