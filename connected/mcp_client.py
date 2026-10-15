# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Stateless modern MCP client used behind the connected bridge boundary."""

from __future__ import annotations

import asyncio
import ipaddress
import os
from contextlib import AsyncExitStack
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse

from atellagent_client.sdk.config import BridgeDeploymentConfig
from .mcp_errors import MCPTargetError

MCP_PROTOCOL_VERSION = "2026-07-28"


def _validate_discovery(result: Mapping[str, Any]) -> None:
    """Fail closed unless the local target speaks the pinned modern revision."""
    if result.get("resultType") != "complete":
        raise MCPTargetError("mcp_target_discovery_invalid", "MCP discovery response must be complete")
    supported_versions = result.get("supportedVersions")
    if (
        not isinstance(supported_versions, list)
        or MCP_PROTOCOL_VERSION not in supported_versions
    ):
        raise MCPTargetError("mcp_target_protocol_unsupported", "MCP target does not support protocol 2026-07-28")
    if result.get("cacheScope") not in {"public", "private"}:
        raise MCPTargetError("mcp_target_discovery_invalid", "MCP discovery response must include a valid cache scope")
    ttl_ms = result.get("ttlMs")
    if not isinstance(ttl_ms, int) or isinstance(ttl_ms, bool) or ttl_ms < 0:
        raise MCPTargetError("mcp_target_discovery_invalid", "MCP discovery response must include a non-negative TTL")
    if not isinstance(result.get("capabilities"), Mapping):
        raise MCPTargetError("mcp_target_discovery_invalid", "MCP discovery response must include capabilities")
    experimental = result["capabilities"].get("experimental", {})
    idempotency = experimental.get("atellagent/durableIdempotency") if isinstance(experimental, Mapping) else None
    if (not isinstance(idempotency, Mapping)
            or idempotency.get("key") != "atellagent/idempotencyKey"
            or idempotency.get("durable") is not True):
        raise MCPTargetError("mcp_target_idempotency_required",
            "MCP target must advertise durable delivery idempotency support; a local assertion is insufficient")


def _require_loopback_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("MCP HTTP bridge target must be a loopback HTTP(S) URL")
    hostname = parsed.hostname.lower()
    if hostname != "localhost":
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError as exc:
            raise ValueError(
                "MCP HTTP bridge target must use localhost or a loopback IP"
            ) from exc
        if not address.is_loopback:
            raise ValueError("MCP HTTP bridge target must use a loopback IP")
    return parsed.geturl()


class LocalMCPClient:
    """Own a reusable local transport; each MCP operation is self-describing."""

    def __init__(self, deployment: BridgeDeploymentConfig) -> None:
        if deployment.target_transport not in {"stdio", "http"}:
            raise MCPTargetError("mcp_target_transport_unsupported", "LocalMCPClient requires a stdio or HTTP bridge target")
        self._deployment = deployment
        self._owner_task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._client: Any = None
        self._connect_lock = asyncio.Lock()

    def _http_headers(self) -> Dict[str, str]:
        headers = dict(self._deployment.upstream_headers)
        token_env = str(self._deployment.upstream_auth_token_env or "").strip()
        if token_env:
            token = os.getenv(token_env)
            if not token:
                raise ValueError(
                    f"Required MCP target credential environment variable is unset: {token_env}"
                )
            header = str(
                self._deployment.upstream_auth_header or "Authorization"
            ).strip()
            headers[header] = token
        return headers

    async def _http_client(self, stack: AsyncExitStack) -> Any:
        import httpx2

        certificate = None
        if self._deployment.upstream_cert_path:
            certificate = (
                self._deployment.upstream_cert_path,
                self._deployment.upstream_key_path,
            )
        transport = httpx2.AsyncHTTPTransport(
            verify=self._deployment.upstream_ca_path or True,
            cert=certificate,
            trust_env=False,
            uds=self._deployment.target_unix_socket,
        )
        return await stack.enter_async_context(
            httpx2.AsyncClient(
                transport=transport,
                headers=self._http_headers(),
                trust_env=False,
            )
        )

    async def _own_connection(self, ready: asyncio.Future, stop: asyncio.Event) -> None:
        try:
            try:
                from mcp import Client, StdioServerParameters
                from mcp.client.stdio import stdio_client
                from mcp.client.streamable_http import streamable_http_client
            except ImportError as exc:
                raise MCPTargetError("mcp_target_dependency_missing",
                    "MCP bridge support requires the 'mcp' package extra"
                ) from exc

            async with AsyncExitStack() as stack:
                if self._deployment.target_transport == "stdio":
                    environment: Dict[str, str] = {}
                    for target_name, source_name in self._deployment.target_env_map.items():
                        value = os.getenv(source_name)
                        if value is None:
                            raise ValueError(
                                f"Required MCP target environment variable is unset: {source_name}"
                            )
                        environment[target_name] = value
                    parameters = StdioServerParameters(
                        command=str(self._deployment.target_command),
                        args=list(self._deployment.target_args),
                        env=environment or None,
                    )
                    transport = stdio_client(parameters)
                else:
                    target_url = _require_loopback_url(
                        str(self._deployment.target_url or "")
                    )
                    http_client = await self._http_client(stack)
                    transport = streamable_http_client(
                        target_url,
                        http_client=http_client,
                        terminate_on_close=False,
                    )
                async with asyncio.timeout(20):
                    client = await stack.enter_async_context(
                        Client(transport, mode=MCP_PROTOCOL_VERSION, cache=None)
                    )
                    discovery = await client.session.send_discover(MCP_PROTOCOL_VERSION)
                _validate_discovery(discovery)
                ready.set_result(client)
                await stop.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            raise

    @staticmethod
    def _observe_result(future: asyncio.Future) -> None:
        if not future.cancelled():
            future.exception()

    async def _stop_connection(self, *, cancel_startup: bool = False) -> None:
        owner = self._owner_task
        self._client = None
        if owner is None:
            return
        self._stop.set()
        if cancel_startup and not owner.done() and not owner.cancelling():
            owner.cancel()
        # Shield the join, not just the cancellation request: cleanup must finish
        # in the task that entered MCP/AnyIO contexts before ownership is dropped.
        joined = asyncio.gather(owner, return_exceptions=True)
        cancelled = None
        while True:
            try:
                outcome = (await asyncio.shield(joined))[0]
                break
            except asyncio.CancelledError as exc:
                cancelled = exc
        if self._owner_task is owner:
            self._owner_task = None
        if cancelled is not None:
            raise cancelled
        if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
            raise outcome

    async def _connect(self) -> Any:
        async with self._connect_lock:
            if self._owner_task is not None and self._owner_task.done():
                await self._stop_connection()
            if self._client is not None:
                return self._client
            ready = asyncio.get_running_loop().create_future()
            ready.add_done_callback(self._observe_result)
            self._stop = asyncio.Event()
            self._owner_task = asyncio.create_task(
                self._own_connection(ready, self._stop), name="local-mcp-connection-owner"
            )
            self._owner_task.add_done_callback(self._observe_result)
            try:
                client = await asyncio.shield(ready)
            except BaseException:
                try:
                    await self._stop_connection(cancel_startup=True)
                except Exception:
                    pass  # Preserve the original startup failure after joining.
                raise
            self._client = client
            return client

    async def invoke(
        self,
        request: Mapping[str, Any],
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """Execute one governed, self-describing modern MCP tools/call."""
        payload = dict(request)
        if payload.get("jsonrpc") != "2.0" or payload.get("method") != "tools/call":
            raise ValueError("connected MCP bridge accepts only JSON-RPC tools/call")
        params = payload.get("params")
        if not isinstance(params, Mapping):
            raise ValueError("MCP tools/call params must be an object")
        tool_name = str(params.get("name") or "").strip()
        arguments = params.get("arguments", {})
        if not tool_name or not isinstance(arguments, Mapping):
            raise ValueError("MCP tools/call requires a tool name and object arguments")
        effect_key = str(idempotency_key or "").strip()
        if not effect_key:
            raise ValueError("MCP bridge idempotency key is required")

        client = await self._connect()
        try:
            result = await client.call_tool(
                tool_name,
                dict(arguments),
                meta={"atellagent/idempotencyKey": effect_key},
            )
        except Exception as exc:
            try:
                await self.close()
            except Exception as cleanup_error:
                exc.add_note(f"MCP cleanup also failed: {cleanup_error!r}")
            raise
        result_payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "result": result_payload,
        }

    async def manifest(self) -> Dict[str, Any]:
        """Read the target's public tool inventory for publication."""
        client = await self._connect()
        async with asyncio.timeout(20):
            result = await client.list_tools(cache_mode="bypass")
        tools = [
            tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in result.tools
        ]
        return {"tools": tools}

    async def close(self) -> None:
        async with self._connect_lock:
            await self._stop_connection()

    async def __aenter__(self) -> "LocalMCPClient":
        await self._connect()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        await self.close()


__all__ = ["LocalMCPClient"]
