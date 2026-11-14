# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under LICENSE.md in this source tree.
"""Credential-local target configuration for the shared MCP adapter."""

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

@dataclass(frozen=True)
class MCPToolTarget:
    """One local or private MCP target; the wire revision is intentionally absent."""

    endpoint_url: str
    credential_headers: Mapping[str, str] | None = None

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
        object.__setattr__(self, "endpoint_url", endpoint)
        object.__setattr__(self, "credential_headers", headers)
