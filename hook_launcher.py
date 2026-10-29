# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Small stdlib-only command boundary around the ordinary hook adapter.

Import and interpreter failures in the child must not turn into a nonblocking
host error. This launcher does not authenticate, evaluate policy, or rewrite
healthy adapter decisions. A missing/tampered launcher itself remains outside
this cooperative hook boundary.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Sequence

from .hook_limits import MAX_HOST_INPUT_BYTES

_ADAPTER_MODULE = "atellagent_client.integrations.agents.host_hooks"
_DEADLINE_SECONDS = 19.0
_MAX_INPUT_BYTES = MAX_HOST_INPUT_BYTES
_FAILURE = "Atellagent hook adapter could not start or complete; request denied."
_TIMEOUT_FAILURE = "Atellagent hook adapter timed out; request denied."
_PRETOOL_TIMEOUT_FAILURE = (
    "Atellagent preflight timed out. The tool was not executed. You may retry "
    "this unchanged tool call once; if it times out again, stop and report the failure."
)
_POSTFLIGHT_TIMEOUT_FAILURE = (
    "Atellagent post-tool reporting timed out. The tool may already have executed; "
    "its outcome was not confirmed. Do not repeat the tool solely because of this audit error."
)


def _timeout_failure(payload: bytes) -> str:
    """Explain our elapsed worker deadline without echoing untrusted input.

    Pre-tool denial and post-tool reporting are different checkpoints. A
    reporting timeout cannot justify re-executing an already attempted tool.
    This helper grants no authority; exact goal recovery is checked separately.
    """
    try:
        event = json.loads(payload)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return _TIMEOUT_FAILURE
    if not isinstance(event, dict):
        return _TIMEOUT_FAILURE
    checkpoint = event.get("hook_event_name")
    if checkpoint in ("PreToolUse", "BeforeTool"):
        return _PRETOOL_TIMEOUT_FAILURE
    if checkpoint in ("PostToolUse", "PostToolUseFailure"):
        return _POSTFLIGHT_TIMEOUT_FAILURE
    return _TIMEOUT_FAILURE


def _goal_recovery(host: str, payload: bytes) -> bool:
    if host != "codex":
        return False
    try:
        event = json.loads(payload)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return False
    return (
        isinstance(event, dict)
        and isinstance(event.get("hook_event_name"), str)
        and event["hook_event_name"] in {"PreToolUse", "PostToolUse"}
        and event.get("tool_name") == "update_goal"
        and event.get("tool_input") in (
            {"status": "blocked"}, {"status": "paused"}, {"status": "complete"},
        )
        and all(isinstance(event.get(key), str) and event[key].strip()
                and len(event[key]) <= 512 for key in ("session_id", "tool_use_id"))
    )


def _pretool_denial(host: str, payload: bytes, reason: str) -> bytes | None:
    if host not in {"codex", "claude-code"}:
        return None
    try:
        event = json.loads(payload)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(event, dict) or event.get("hook_event_name") != "PreToolUse":
        return None
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, separators=(",", ":")).encode() + b"\n"


def _emit_failure(host: str, payload: bytes, reason: str) -> None:
    denial = _pretool_denial(host, payload, reason)
    if denial is not None:
        sys.stdout.buffer.write(denial)
        raise SystemExit(0)
    print(reason, file=sys.stderr)
    raise SystemExit(2)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Atellagent guarded command hook")
    parser.add_argument("--host", required=True, choices=("codex", "claude-code", "gemini-cli"))
    parser.add_argument("--socket", required=True)
    args = parser.parse_args(argv)
    payload = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if len(payload) > _MAX_INPUT_BYTES:
        print(_FAILURE, file=sys.stderr)
        raise SystemExit(2)
    timed_out = False
    try:
        command = ([sys.executable, "hook-worker"] if getattr(sys, "frozen", False)
            else [sys.executable, "-c", f"from {_ADAPTER_MODULE} import main; main()"])
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        child = subprocess.run(
            [*command, "--host", args.host, "--socket", args.socket],
            input=payload, capture_output=True, timeout=_DEADLINE_SECONDS, check=False,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        child = None
    except OSError:
        child = None
    if child is not None and child.returncode == 0:
        # In particular, preserve healthy JSON permission-deny and approval
        # decisions. Exit zero means the adapter ran, not that policy allowed.
        sys.stdout.buffer.write(child.stdout)
        sys.stderr.buffer.write(child.stderr)
        raise SystemExit(0)
    if child is not None and child.returncode == 2:
        # A completed worker refusal remains a refusal, including for goals.
        # The ordinary worker handles operational goal recovery. Pre-tool
        # refusals need an explicit denial; partial output is never forwarded.
        denial = _pretool_denial(args.host, payload, _FAILURE)
        if denial is not None:
            # A failed worker supplies no trusted pre-tool decision or safe
            # partial output. Explicitly deny without echoing its diagnostics.
            sys.stdout.buffer.write(denial)
            raise SystemExit(0)
        sys.stderr.buffer.write(child.stderr)
        raise SystemExit(2)
    if _goal_recovery(args.host, payload):
        # Recover only when the worker could not start or finish, rather than
        # overriding a documented refusal from a functioning worker.
        raise SystemExit(0)
    # Never expose a Python traceback, partial stdout, or raw input from a
    # failed child. Recognized pre-tool failures use the explicit denial path.
    _emit_failure(args.host, payload, _timeout_failure(payload) if timed_out else _FAILURE)


if __name__ == "__main__":
    main()
