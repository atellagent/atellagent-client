# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Attended, one-server MCP tool-list probe; ordinary discovery never connects."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any, Mapping
from urllib.parse import urlsplit

from .discovery_probe_transport import ProbeDependencyMissing, _http_url, list_probe_tools


from atellagent_client.discovery.targets import ProbeTarget, select_target


def _destination(target: ProbeTarget) -> str:
    if target.transport == "stdio":
        command = target.config.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("The selected server has no executable command")
        if len(command) > 512 or any(ord(character) < 32 for character in command):
            raise ValueError("The selected server executable is invalid")
        return f"local executable {json.dumps(command)} (arguments and environment hidden)"
    parsed = urlsplit(_http_url(target.config))
    hostname = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{hostname}{':' + str(parsed.port) if parsed.port else ''} (path and query hidden)"


def run(arguments: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="atellagent-cli discover probe",
        description="Explicitly connect to one configured MCP server and list tool names only.",
    )
    parser.add_argument("--host", required=True, choices=("codex", "claude-code"))
    parser.add_argument("--name", required=True, help="Exact discovered MCP server name")
    parser.add_argument("--source", help="Source label when the name occurs more than once")
    parser.add_argument("--project", type=Path, help="Also consider this explicitly selected project")
    args = parser.parse_args(arguments)
    try:
        target = select_target(
            host=args.host, name=args.name, home=Path.home(),
            project=args.project, source=args.source,
        )
        destination = _destination(target)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"[ERROR] {exc}\n")
    print(f"Selected {args.host}/{args.name} from {target.source}: {destination}")
    print("This probe may start the configured process or contact the shown host. "
          "It only negotiates MCP and lists tool names; it never invokes a tool, "
          "registers an integration, or starts OAuth.")
    if not sys.stdin.isatty() or input(f"Type {args.name} to probe: ").strip() != args.name:
        print("Probe cancelled; no connection was made.")
        return
    try:
        names = asyncio.run(list_probe_tools(target.transport, target.config))
    except ProbeDependencyMissing as exc:
        parser.exit(1, f"[ERROR] {exc}\n")
    except (Exception, KeyboardInterrupt) as exc:
        reason = "timed out" if isinstance(exc, TimeoutError) else "failed"
        parser.exit(1, f"[ERROR] MCP probe {reason}; no tool was invoked.\n")
    print(f"Showing {len(names)} tool names (up to 100; discovered, not approved):")
    for name in names:
        print(f"  {name}")


__all__ = ["ProbeTarget", "select_target", "run"]
