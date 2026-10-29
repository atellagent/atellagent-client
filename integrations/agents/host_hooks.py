# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Thin command-hook adapters for supported external coding hosts.

Each invocation reads one documented host JSON object from stdin, calls the
credential-free local hook-control endpoint, and emits only the host's documented
hook result.  This module deliberately contains no policy logic, credentials,
or service implementation detail.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Mapping, Sequence
from atellagent_client.hook_limits import MAX_HOST_INPUT_BYTES
from .hook_control import HookControlError
from .host_hook_contracts import (
    ADAPTER_DEADLINE_SECONDS,
    HOST_HOOK_ADAPTER_VERSION,
    HookAdapterResponse,
    _HOST_NAMES,
    host_hook_capabilities,
)
from .host_hook_events import _string, _tool_fields
from .host_hook_responses import _failure, _pretool_result, _pretool_failure
from .host_hook_recovery import (
    _is_codex_goal_lifecycle_transition,
    _goal_lifecycle_recovery_allowed,
)
from .host_hook_transport import _valid_control_endpoint
from .host_hook_gemini import _handle_gemini_before_model, _handle_gemini_before_tool
from .host_hook_tools import _handle_prompt, _handle_pretool, _handle_posttool

_MAX_STDIN_BYTES = MAX_HOST_INPUT_BYTES


def _event_failure(host: str, event: Mapping[str, Any], exc: Exception | None = None) -> HookAdapterResponse:
    if host in {"codex", "claude-code"} and event.get("hook_event_name") == "PreToolUse":
        return _pretool_failure(host, exc)
    return _failure(exc)


async def handle_host_hook(
    host: str,
    socket_path: str,
    event: Mapping[str, Any],
) -> HookAdapterResponse:
    """Translate one supported host event to the local hook-control protocol."""
    goal_lifecycle = host == "codex" and _is_codex_goal_lifecycle_transition(event)
    if goal_lifecycle:
        try:
            _tool_fields(host, event)
        except HookControlError as exc:
            return _event_failure(host, event, exc)
    if host not in _HOST_NAMES or not _valid_control_endpoint(socket_path):
        return HookAdapterResponse(exit_code=0) if goal_lifecycle else _event_failure(host, event)
    try:
        event_name = _string(event, "hook_event_name")
        if host == "gemini-cli":
            if event_name == "BeforeModel":
                return await _handle_gemini_before_model(socket_path, event)
            if event_name == "BeforeTool":
                return await _handle_gemini_before_tool(socket_path, event)
            return _failure()
        if event_name == "UserPromptSubmit":
            return await _handle_prompt(host, socket_path, event)
        if event_name == "PreToolUse":
            return await _handle_pretool(host, socket_path, event)
        if event_name in {"PostToolUse", "PostToolUseFailure"}:
            if host == "codex" and event_name != "PostToolUse":
                return _failure()
            if host not in {"claude-code", "codex"}:
                return _failure()
            return await _handle_posttool(host, socket_path, event)
        return _failure()
    except (asyncio.TimeoutError, HookControlError, ValueError, TypeError) as exc:
        if goal_lifecycle and _goal_lifecycle_recovery_allowed(exc):
            return HookAdapterResponse(exit_code=0)
        if event.get("hook_event_name") in {"PreToolUse", "BeforeTool"} and (
            isinstance(exc, asyncio.TimeoutError)
            or isinstance(exc, HookControlError)
            and exc.code == "control_timeout"
        ):
            return _pretool_result(host, False, "control_timeout")
        return _event_failure(host, event, exc)
    except Exception as exc:
        # Only exact terminal goal updates may recover from controller errors.
        return HookAdapterResponse(exit_code=0) if goal_lifecycle else _event_failure(host, event, exc)


def _parse_stdin() -> Mapping[str, Any]:
    raw = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
    if not raw or len(raw) > _MAX_STDIN_BYTES:
        raise ValueError("invalid hook input")
    value = json.loads(raw)
    if not isinstance(value, Mapping):
        raise ValueError("invalid hook input")
    return value


def main(argv: Sequence[str] | None = None) -> None:
    """Run one command-hook invocation."""
    parser = argparse.ArgumentParser(
        description="Atellagent external-agent hook adapter"
    )
    parser.add_argument("--host", choices=sorted(_HOST_NAMES), required=True)
    parser.add_argument(
        "--socket",
        required=True,
        help="absolute Unix socket path or local Windows named-pipe endpoint",
    )
    args = parser.parse_args(argv)
    try:
        response = asyncio.run(handle_host_hook(args.host, args.socket, _parse_stdin()))
    except (ValueError, TypeError, json.JSONDecodeError):
        response = _failure(HookControlError("hook_input_invalid"))
    if response.stdout:
        print(response.stdout)
    if response.stderr:
        print(response.stderr, file=sys.stderr)
    raise SystemExit(response.exit_code)


__all__ = [
    "HookAdapterResponse",
    "ADAPTER_DEADLINE_SECONDS",
    "HOST_HOOK_ADAPTER_VERSION",
    "handle_host_hook",
    "host_hook_capabilities",
    "main",
]


if __name__ == "__main__":
    main()
