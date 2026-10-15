# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Short-lived MCP negotiation and tool-name listing, without tool invocation."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import ipaddress
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from atellagent_client.discovery.inventory import _safe_name


_PROBE_TIMEOUT_SECONDS = 10


class ProbeDependencyMissing(RuntimeError):
    """The optional MCP probe transport is not installed."""


def _stdio_parameters(config: Mapping[str, Any], parameter_type: Any) -> Any:
    command = config.get("command")
    args = config.get("args", [])
    environment = config.get("env", {})
    if (not isinstance(command, str) or not command.strip() or len(command) > 512
            or any(ord(character) < 32 for character in command)):
        raise ValueError("MCP probe command is invalid")
    if (not isinstance(args, list) or len(args) > 64
            or any(not isinstance(arg, str) or len(arg) > 4096 for arg in args)):
        raise ValueError("MCP probe arguments are invalid")
    if (not isinstance(environment, dict) or len(environment) > 64
            or any(not isinstance(key, str) or not key or len(key) > 128
                   or not isinstance(value, str) or len(value) > 8192
                   for key, value in environment.items())):
        raise ValueError("MCP probe environment is invalid")
    return parameter_type(command=command, args=args, env=environment or None)


def _http_url(config: Mapping[str, Any]) -> str:
    value = config.get("url")
    if (not isinstance(value, str) or len(value) > 4096
            or any(ord(character) < 32 for character in value)):
        raise ValueError("MCP probe URL is invalid")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment):
        raise ValueError("MCP probe URL is invalid")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("MCP probe URL port is invalid") from exc
    if port is not None and port < 1:
        raise ValueError("MCP probe URL port is invalid")
    if parsed.scheme == "http":
        hostname = parsed.hostname.lower()
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname == "localhost"
        if not loopback:
            raise ValueError("Remote MCP probing requires HTTPS")
    return value


async def list_probe_tools(transport_kind: str, config: Mapping[str, Any]) -> list[str]:
    """Never supply OAuth/auth callbacks or call a tool; return bounded names."""

    try:
        from mcp import Client, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.client.sse import sse_client
        import httpx2
    except ImportError as exc:
        raise ProbeDependencyMissing(
            "MCP probing requires the atellagent-client[mcp] extra"
        ) from exc

    async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
        async with AsyncExitStack() as stack:
            if transport_kind == "stdio":
                parameters = _stdio_parameters(config, StdioServerParameters)
                errlog = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
                transport = stdio_client(parameters, errlog=errlog)
            elif transport_kind in {"http", "sse"}:
                url = _http_url(config)

                def http_client(headers=None, timeout=None, auth=None):
                    if auth is not None:
                        raise ValueError("MCP probe does not accept an OAuth handler")
                    return httpx2.AsyncClient(
                        transport=httpx2.AsyncHTTPTransport(trust_env=False),
                        headers=headers, timeout=timeout or _PROBE_TIMEOUT_SECONDS,
                        auth=auth, trust_env=False, follow_redirects=False,
                    )

                if transport_kind == "sse":
                    transport = sse_client(
                        url, timeout=5, sse_read_timeout=_PROBE_TIMEOUT_SECONDS,
                        httpx_client_factory=http_client,
                    )
                else:
                    session = await stack.enter_async_context(http_client())
                    transport = streamable_http_client(
                        url, http_client=session, terminate_on_close=False,
                    )
            else:
                raise ValueError("MCP probe transport is unsupported")
            async with Client(
                transport, mode="auto", cache=None,
                read_timeout_seconds=_PROBE_TIMEOUT_SECONDS,
            ) as client:
                names = []
                cursor = None
                seen_cursors = set()
                for _ in range(100):
                    result = await client.list_tools(cursor=cursor, cache_mode="bypass")
                    for tool in result.tools:
                        name = _safe_name(getattr(tool, "name", None))
                        if name is not None and name not in names:
                            names.append(name)
                        if len(names) == 100:
                            return names
                    cursor = getattr(result, "next_cursor", None)
                    if not cursor or cursor in seen_cursors:
                        break
                    seen_cursors.add(cursor)
                return names


__all__ = ["ProbeDependencyMissing", "list_probe_tools"]
