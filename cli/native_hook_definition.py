"""Reviewable generated settings fragments, not host writes or readiness evidence."""

from hashlib import sha256
import json
from pathlib import PurePosixPath, PureWindowsPath

from .native_hook_commands import command


def definition(*, draft, executable, endpoint, home, platform):
    host = draft.proposal.native_host
    argv = [str(executable), "hook", "--host", host, "--socket", endpoint]
    handler = command(argv, platform=platform, host=host)
    path = PureWindowsPath if platform == "win32" else PurePosixPath
    root = path(home)
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("absolute current-user home required")
    events = ["UserPromptSubmit", "PreToolUse", "PostToolUse"]
    if host == "claude-code":
        events.append("PostToolUseFailure")
    labels = {"UserPromptSubmit": "Atellagent: checking prompt",
        "PreToolUse": "Atellagent: checking tool action", "PostToolUse": "Atellagent: recording tool result"}
    hooks = {event: [{"hooks": [{"type": "command", **handler, "timeout": 20,
        **({"statusMessage": labels[event]} if host == "codex" else {})}]}] for event in events}
    settings = root / (".codex" if host == "codex" else ".claude")
    files = [dict(path=str(settings / ("hooks.json" if host == "codex" else "settings.json")),
        format="json", fragment={"hooks": hooks})]
    if platform == "darwin":
        bridge = {"command": str(executable), "args": ["mcp-bridge", "--socket", endpoint]}
        if host == "codex":
            files.append(dict(path=str(settings / "config.toml"), format="toml",
                fragment={"network": {"allow_unix_sockets": [endpoint]},
                    "mcp_servers": {"atellagent": {**bridge, "startup_timeout_sec": 30}}}))
        else:
            files.append(dict(path=str(root / ".claude.json"), format="json",
                fragment={"mcpServers": {"atellagent": {"type": "stdio", **bridge, "alwaysLoad": True}}}))
    result = dict(request_id=str(draft.proposal.id), device_id=str(draft.device_id),
        native_host=host, endpoint=endpoint, command_argv=argv, files=files, readiness="not_verified")
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    return {**result, "definition_sha256": sha256(canonical).hexdigest()}
