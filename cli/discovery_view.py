# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Human-readable view of the bounded, local discovery result."""

from __future__ import annotations

from typing import Any


_HOST_LABELS = {"codex": "Codex", "claude-code": "Claude Code"}


def format_inventory(inventory: dict[str, Any]) -> str:
    lines = [
        "Current-user discovery (read-only; no network request or host change)",
        "Supported sources: Codex and Claude Code user settings",
    ]
    if inventory.get("project_selected"):
        lines.append("Also inspected: the project you selected explicitly")
    for host in inventory.get("hosts", []):
        label = _HOST_LABELS.get(host.get("host"))
        if label is None:
            continue
        present = bool(host.get("executable_on_path") or host.get("config_detected"))
        lines.append(f"\n{label}: {'detected' if present else 'not detected'}")
        lines.append(f"  CLI command on this process's PATH: {'found' if host.get('executable_on_path') else 'not found'}")
        lines.append(f"  Settings: {'detected' if host.get('config_detected') else 'not detected'}")
        lines.append(f"  Atellagent hooks: {'detected' if host.get('atellagent_hooks_detected') else 'not detected'}")
        servers = host.get("mcp_servers") or []
        lines.append(f"  Declared MCP entries: {len(servers)} (discovered, not approved)")
        for server in servers:
            lines.append(f"    - {server['name']} · {server['transport']} · {server['scope']}")
    lines.extend([
        "",
        "PATH detection may miss shell-managed installs when run from a desktop app.",
        "Discovery does not connect, register, probe, or approve any MCP entry.",
        "To request a coding-host connection on an enrolled device, run",
        "atellagent-cli setup native request --host <codex|claude-code> --name <name>.",
        "Administrator approval and your explicit consent are required before activation.",
    ])
    return "\n".join(lines)


__all__ = ["format_inventory"]
