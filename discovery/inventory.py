# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Read-only, current-user inventory of supported coding-host settings."""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import tomllib
from typing import Any, Mapping


_MAX_CONFIG_BYTES = 1024 * 1024
_MAX_NAME_LENGTH = 128


def _read_config(path: Path, kind: str) -> tuple[Mapping[str, Any] | None, str]:
    """Read one known file, never following symlinks or echoing file content."""

    try:
        info = path.lstat()
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_CONFIG_BYTES:
        return None, "unsupported"
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                return None, "unsupported"
            data = handle.read(_MAX_CONFIG_BYTES + 1)
        if len(data) > _MAX_CONFIG_BYTES:
            return None, "unsupported"
        parsed = tomllib.loads(data.decode("utf-8")) if kind == "toml" else json.loads(data)
    except (OSError, UnicodeError, ValueError):
        return None, "invalid"
    if not isinstance(parsed, dict):
        return None, "invalid"
    return parsed, "read"


def _safe_name(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > _MAX_NAME_LENGTH:
        return None
    if not all(character.isascii() and (character.isalnum() or character in "._-") for character in value):
        return None
    return value


def _mcp_transport(config: Any) -> str:
    if not isinstance(config, dict):
        return "unknown"
    declared = config.get("type")
    if declared == "stdio" or isinstance(config.get("command"), str):
        return "stdio"
    if declared == "http" or declared == "streamable-http" or (
        declared is None and isinstance(config.get("url"), str)
    ):
        return "http"
    if declared == "sse":
        return "sse"
    return "unknown"


def _mcp_entries(document: Mapping[str, Any], key: str, scope: str) -> list[dict[str, str]]:
    servers = document.get(key)
    if not isinstance(servers, dict):
        return []
    entries = []
    for raw_name, config in servers.items():
        name = _safe_name(raw_name)
        if name is not None:
            entries.append({"name": name, "scope": scope, "transport": _mcp_transport(config), "status": "discovered_not_approved"})
    return entries


def _hook_command_matches(command: str, *, host: str) -> bool:
    """Inspect supported launcher syntax without running a shell or executable."""
    if len(command) > 65536:
        return False
    encoded_prefix = "powershell.exe -NoProfile -NonInteractive -EncodedCommand "
    if command.startswith(encoded_prefix):
        try:
            command = base64.b64decode(command[len(encoded_prefix):], validate=True).decode("utf-16-le")
        except (binascii.Error, UnicodeError, ValueError):
            return False
    script_prefix = "$ErrorActionPreference='Stop'; try { & "
    if command.startswith(script_prefix):
        remaining = command[len(script_prefix):]
        arguments = []
        for _ in range(6):
            match = re.match(r"'((?:[^']|'')*)'(?=\s|;)", remaining)
            if match is None:
                return False
            arguments.append(match[1].replace("''", "'"))
            remaining = remaining[match.end():].lstrip()
        if not remaining.startswith("; if "):
            return False
    else:
        try:
            arguments = shlex.split(command)
        except ValueError:
            return False
    if not arguments:
        return False
    executable = arguments[0].replace("\\", "/").rsplit("/", 1)[-1]
    if executable in {"atellagent-cli", "atellagent-cli.exe"}:
        return len(arguments) == 6 and arguments[1:5] == ["hook", "--host", host, "--socket"]
    # These are also current public CLI entry points, not desktop-only commands.
    if executable == "atellagent-hook-adapter":
        options = arguments[1:]
    elif len(arguments) > 2 and arguments[1:3] == ["-m", "atellagent_client.hook_launcher"]:
        options = arguments[3:]
    else:
        return False
    return any(item == "--host=" + host or
               (item == "--host" and index + 1 < len(options) and options[index + 1] == host)
               for index, item in enumerate(options))


def _has_adapter(value: Any, *, host: str) -> bool:
    pending = [(value, 0)]
    inspected = 0
    while pending and inspected < 4096:
        current, depth = pending.pop()
        inspected += 1
        if isinstance(current, dict):
            command = current.get("command")
            if isinstance(command, str) and _hook_command_matches(command, host=host):
                return True
            if depth < 16:
                pending.extend((nested, depth + 1) for nested in current.values() if isinstance(nested, (dict, list)))
        elif isinstance(current, list) and depth < 16:
            pending.extend((nested, depth + 1) for nested in current if isinstance(nested, (dict, list)))
    return False


def _inventory_host(
    host: str, files: list[tuple[str, Path, str, str]], *,
    claude_project_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "host": host,
        "executable_on_path": shutil.which("codex" if host == "codex" else "claude") is not None,
        "config_files": [],
        "atellagent_hooks_detected": False,
        "atellagent_mcp_detected": False,
        "mcp_servers": [],
    }
    for scope, path, kind, role in files:
        document, status = _read_config(path, kind)
        result["config_files"].append({"scope": scope, "role": role, "status": status})
        if document is None:
            continue
        result["atellagent_hooks_detected"] |= _has_adapter(document.get("hooks"), host=host)
        mcp_key = "mcp_servers" if host == "codex" else "mcpServers"
        entries = _mcp_entries(document, mcp_key, scope)
        result["mcp_servers"].extend(entries)
        result["atellagent_mcp_detected"] |= any(item["name"] == "atellagent" for item in entries)
        if host == "claude-code" and role == "user_state" and claude_project_keys:
            projects = document.get("projects")
            if isinstance(projects, dict):
                for project_key in claude_project_keys:
                    project_state = projects.get(project_key)
                    if isinstance(project_state, dict):
                        project_entries = _mcp_entries(project_state, "mcpServers", "project")
                        result["mcp_servers"].extend(project_entries)
                        result["atellagent_mcp_detected"] |= any(item["name"] == "atellagent" for item in project_entries)
    result["config_detected"] = any(item["status"] == "read" for item in result["config_files"])
    result["mcp_servers"].sort(key=lambda item: (item["scope"], item["name"]))
    return result


def discover(*, home: Path, project: Path | None = None) -> dict[str, Any]:
    """Inspect fixed per-user locations, plus one explicitly selected project."""

    home = home.resolve()
    project_key = str(project.expanduser().absolute()) if project is not None else None
    project = project.resolve(strict=True) if project is not None else None
    if project is not None and not project.is_dir():
        raise ValueError("Project must be an existing directory")
    codex_files = [
        ("user", home / ".codex" / "config.toml", "toml", "settings"),
        ("user", home / ".codex" / "hooks.json", "json", "hooks"),
    ]
    claude_files = [
        ("user", home / ".claude" / "settings.json", "json", "settings"),
        ("user", home / ".claude.json", "json", "user_state"),
    ]
    if project is not None:
        codex_files.extend([
            ("project", project / ".codex" / "config.toml", "toml", "settings"),
            ("project", project / ".codex" / "hooks.json", "json", "hooks"),
        ])
        claude_files.extend([
            ("project", project / ".claude" / "settings.json", "json", "settings"),
            ("project", project / ".claude" / "settings.local.json", "json", "settings"),
            ("project", project / ".mcp.json", "json", "mcp"),
        ])
    return {
        "schema_version": 1,
        "read_only": True,
        "project_selected": project is not None,
        "hosts": [
            _inventory_host("codex", codex_files),
            _inventory_host(
                "claude-code", claude_files,
                claude_project_keys=tuple(dict.fromkeys((project_key, str(project)))) if project is not None else (),
            ),
        ],
    }
