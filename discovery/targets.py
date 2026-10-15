"""Exact read-only MCP source selection, shared by discovery and setup."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .inventory import _mcp_transport, _read_config, _safe_name


@dataclass(frozen=True)
class ProbeTarget:
    source: str
    transport: str
    config: Mapping[str, Any]


def _sources(host: str, home: Path, project: Path | None):
    if host == "codex":
        yield "codex-user", home / ".codex" / "config.toml", "toml", "mcp_servers", None
        if project is not None:
            yield "codex-project", project / ".codex" / "config.toml", "toml", "mcp_servers", None
        return
    yield "claude-user-state", home / ".claude.json", "json", "mcpServers", None
    yield "claude-user-settings", home / ".claude" / "settings.json", "json", "mcpServers", None
    if project is not None:
        yield "claude-project-mcp", project / ".mcp.json", "json", "mcpServers", None
        yield "claude-project-settings", project / ".claude" / "settings.json", "json", "mcpServers", None
        yield "claude-project-local", project / ".claude" / "settings.local.json", "json", "mcpServers", None
        yield "claude-project-state", home / ".claude.json", "json", "mcpServers", project


def select_target(
    *, host: str, name: str, home: Path, project: Path | None = None,
    source: str | None = None,
) -> ProbeTarget:
    if host not in {"codex", "claude-code"} or _safe_name(name) != name:
        raise ValueError("Select a supported host and a plain discovered server name")
    home = home.resolve()
    project_key = str(project.expanduser().absolute()) if project is not None else None
    project = project.resolve(strict=True) if project is not None else None
    if project is not None and not project.is_dir():
        raise ValueError("The selected project must be a directory")
    candidates = []
    for label, path, kind, key, project_state in _sources(host, home, project):
        if source is not None and source != label:
            continue
        document, status = _read_config(path, kind)
        if document is None:
            if source == label and status not in {"missing"}:
                raise ValueError("The selected source cannot be read safely")
            continue
        if project_state is not None:
            projects = document.get("projects")
            if not isinstance(projects, dict):
                continue
            selected = None
            for project_name in dict.fromkeys((project_key, str(project_state))):
                value = projects.get(project_name)
                if isinstance(value, dict):
                    selected = value
                    break
            document = selected or {}
        servers = document.get(key)
        config = servers.get(name) if isinstance(servers, dict) else None
        if isinstance(config, dict):
            candidates.append(ProbeTarget(label, _mcp_transport(config), config))
    if len(candidates) != 1:
        if not candidates:
            raise ValueError("No matching server was found in the selected current-user source")
        labels = ", ".join(item.source for item in candidates)
        raise ValueError(f"Server name appears in multiple sources ({labels}); pass --source")
    target = candidates[0]
    if target.transport not in {"stdio", "http", "sse"}:
        raise ValueError("The selected server has no supported probe transport")
    return target
