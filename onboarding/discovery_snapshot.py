# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Allowlisted current-user observations, never configuration or authority."""

from datetime import datetime, timezone
from pathlib import Path
import re

from atellagent_client._version import CLIENT_LIBRARY_VERSION
from atellagent_client.discovery.inventory import discover


def discovery_snapshot(*, home: Path | None = None) -> dict:
    # No project selection, probe, recursive scan or remote connection.
    inventory = discover(home=Path.home() if home is None else home)
    hosts, seen = [], set()
    for item in inventory.get("hosts", []):
        host = item.get("host")
        if host not in {"codex", "claude-code"} or host in seen:
            continue
        seen.add(host)
        servers = []
        for server in item.get("mcp_servers", []):
            name, transport = server.get("name"), server.get("transport")
            if (isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name)
                and transport in {"stdio", "http", "sse", "unknown"}):
                servers.append({"name": name, "transport": transport})
                if len(servers) == 64:
                    break
        hosts.append({"host": host, "mcp_servers": servers, **{
            key: bool(item.get(key)) for key in (
                "executable_on_path", "config_detected", "atellagent_hooks_detected",
                "atellagent_mcp_detected")}})
    return {"hosts": hosts, "client_version": CLIENT_LIBRARY_VERSION,
        "client_observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
