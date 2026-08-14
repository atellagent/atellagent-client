# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Thin command-hook adapters for supported external coding hosts.

Each invocation reads one documented host JSON object from stdin, calls the
credential-free local hook-control socket, and emits only the host's documented
hook result.  This module deliberately contains no policy logic, credentials,
or service implementation detail.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .hook_control import HookControlClient, HookControlError


INTERNAL_DEADLINE_SECONDS = 7.0
"""Bounded local-control deadline; host templates use an 8 second timeout."""

_DENIED_REASON = "Atellagent policy denied this request."
_UNAVAILABLE_REASON = "Atellagent control is unavailable; request denied."
_MAX_STDIN_BYTES = 64 * 1024
_HOST_NAMES = {"claude-code", "codex", "gemini-cli"}
_ATELLAGENT_MCP_PREFIX = "mcp__atellagent__"


@dataclass(frozen=True)
class HookAdapterResponse:
    """A complete host command-hook response."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


def host_hook_capabilities() -> dict[str, Any]:
    """Return the public machine-readable coverage declaration."""
    return {
        "schema_version": "atellagent.host-hooks.v1",
        "internal_deadline_seconds": INTERNAL_DEADLINE_SECONDS,
        "hosts": {
            "claude-code": {
                "transport": "command",
                "events": {
                    "UserPromptSubmit": "turn_entry",
                    "PreToolUse": "preflight",
                    "PostToolUse": "postflight_success",
                    "PostToolUseFailure": "postflight_failure",
                },
                "exclusions": ["mcp__atellagent__* (effect-boundary MCP PEP)"],
            },
            "codex": {
                "transport": "command",
                "events": {
                    "UserPromptSubmit": "turn_entry",
                    "PreToolUse": "preflight",
                    "PostToolUse": "postflight_success",
                },
                "exclusions": [
                    "mcp__atellagent__* (effect-boundary MCP PEP)",
                    "hosted or specialized tool paths outside documented command hooks",
                ],
            },
            "gemini-cli": {
                "transport": "command",
                "events": {
                    "BeforeModel": "turn_entry",
                    "BeforeTool": "preflight",
                },
                "exclusions": [
                    "AfterModel (not supported)",
                    "tool outcomes without a stable host tool-call identifier",
                ],
            },
        },
    }


def _failure() -> HookAdapterResponse:
    return HookAdapterResponse(exit_code=2, stderr=_UNAVAILABLE_REASON)


def _string(event: Mapping[str, Any], field: str) -> str:
    value = event.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid hook input")
    return value.strip()


def _event(event: Mapping[str, Any], expected: Sequence[str]) -> str:
    event_name = _string(event, "hook_event_name")
    if event_name not in expected:
        raise ValueError("unsupported hook event")
    return event_name


def _turn_id(host: str, event: Mapping[str, Any], *, fallback: str) -> str:
    supplied = event.get("turn_id")
    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip()
    # Claude Code's hook schema does not provide a turn ID for every event.
    # A deterministic, bounded correlation value adds no authority and keeps
    # preflight/postflight pairing stable for one host-provided event identity.
    material = f"{host}:{_string(event, 'session_id')}:{fallback}".encode("utf-8")
    return f"hook-{sha256(material).hexdigest()}"


def _arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    # The Codex schema permits any JSON value for tool_input. The daemon's
    # public protocol intentionally uses object facts, so preserve such input
    # under one neutral field instead of interpreting it locally.
    return {"input": value}


def _is_atellagent_mcp_facade(tool_name: str) -> bool:
    return tool_name.startswith(_ATELLAGENT_MCP_PREFIX)


async def _call(socket_path: str, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
    client = HookControlClient(socket_path, timeout_seconds=INTERNAL_DEADLINE_SECONDS)
    return await asyncio.wait_for(
        client.call(method, params), timeout=INTERNAL_DEADLINE_SECONDS
    )


def _allowed(result: Mapping[str, Any]) -> bool:
    value = result.get("allowed")
    if not isinstance(value, bool):
        raise HookControlError("control_unavailable")
    return value


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _prompt_result(host: str, allowed: bool) -> HookAdapterResponse:
    if allowed:
        return HookAdapterResponse(exit_code=0)
    return HookAdapterResponse(
        exit_code=0,
        stdout=_json({"decision": "block", "reason": _DENIED_REASON}),
    )


def _pretool_result(host: str, allowed: bool) -> HookAdapterResponse:
    if host == "gemini-cli":
        if allowed:
            return HookAdapterResponse(exit_code=0)
        return HookAdapterResponse(
            exit_code=0,
            stdout=_json({"decision": "deny", "reason": _DENIED_REASON}),
        )
    # Both supported host command-hook schemas use this documented shape.
    output: dict[str, Any] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow" if allowed else "deny",
    }
    if not allowed:
        output["permissionDecisionReason"] = _DENIED_REASON
    return HookAdapterResponse(
        exit_code=0,
        stdout=_json({"hookSpecificOutput": output}),
    )


def _gemini_turn_id(event: Mapping[str, Any], *, fallback: str) -> str:
    timestamp = _string(event, "timestamp")
    material = f"gemini-cli:{_string(event, 'session_id')}:{timestamp}:{fallback}".encode("utf-8")
    return f"hook-{sha256(material).hexdigest()}"


async def _handle_gemini_before_model(
    socket_path: str, event: Mapping[str, Any]
) -> HookAdapterResponse:
    _event(event, ("BeforeModel",))
    request = event.get("llm_request")
    if not isinstance(request, Mapping) or not isinstance(request.get("messages"), list):
        raise ValueError("invalid hook input")
    session_id = _string(event, "session_id")
    messages = list(request["messages"])
    turn_id = _gemini_turn_id(event, fallback=json.dumps(messages, sort_keys=True, separators=(",", ":")))
    result = await _call(
        socket_path,
        "model.decision",
        {
            "host": "gemini_cli",
            "session_id": session_id,
            "turn_id": turn_id,
            "messages": messages,
        },
    )
    return _prompt_result("gemini-cli", _allowed(result))


async def _handle_gemini_before_tool(
    socket_path: str, event: Mapping[str, Any]
) -> HookAdapterResponse:
    _event(event, ("BeforeTool",))
    session_id = _string(event, "session_id")
    tool_name = _string(event, "tool_name")
    arguments = event.get("tool_input")
    if not isinstance(arguments, Mapping):
        raise ValueError("invalid hook input")
    tool_call_id = _gemini_turn_id(
        event,
        fallback=f"{tool_name}:{json.dumps(dict(arguments), sort_keys=True, separators=(',', ':'))}",
    )
    result = await _call(
        socket_path,
        "action.preflight",
        {
            "host": "gemini_cli",
            "session_id": session_id,
            "turn_id": tool_call_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": dict(arguments),
            "postflight_required": False,
        },
    )
    return _pretool_result("gemini-cli", _allowed(result))


async def _handle_prompt(host: str, socket_path: str, event: Mapping[str, Any]) -> HookAdapterResponse:
    _event(event, ("UserPromptSubmit",))
    prompt = event.get("prompt", event.get("user_prompt"))
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("invalid hook input")
    session_id = _string(event, "session_id")
    turn_id = _turn_id(host, event, fallback=prompt)
    result = await _call(
        socket_path,
        "model.decision",
        {
            "host": host.replace("-", "_"),
            "session_id": session_id,
            "turn_id": turn_id,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    return _prompt_result(host, _allowed(result))


def _tool_fields(host: str, event: Mapping[str, Any]) -> tuple[str, str, str, str]:
    session_id = _string(event, "session_id")
    tool_call_id = _string(event, "tool_use_id")
    tool_name = _string(event, "tool_name")
    return session_id, _turn_id(host, event, fallback=tool_call_id), tool_call_id, tool_name


async def _handle_pretool(host: str, socket_path: str, event: Mapping[str, Any]) -> HookAdapterResponse:
    _event(event, ("PreToolUse",))
    session_id, turn_id, tool_call_id, tool_name = _tool_fields(host, event)
    if _is_atellagent_mcp_facade(tool_name):
        return _pretool_result(host, True)
    result = await _call(
        socket_path,
        "action.preflight",
        {
            "host": host.replace("-", "_"),
            "session_id": session_id,
            "turn_id": turn_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": _arguments(event.get("tool_input")),
        },
    )
    return _pretool_result(host, _allowed(result))


async def _handle_posttool(host: str, socket_path: str, event: Mapping[str, Any]) -> HookAdapterResponse:
    event_name = _event(event, ("PostToolUse", "PostToolUseFailure"))
    session_id, turn_id, tool_call_id, tool_name = _tool_fields(host, event)
    if _is_atellagent_mcp_facade(tool_name):
        return HookAdapterResponse(exit_code=0)
    success = event_name == "PostToolUse"
    params: dict[str, Any] = {
        "host": host.replace("-", "_"),
        "session_id": session_id,
        "turn_id": turn_id,
        "tool_call_id": tool_call_id,
        "success": success,
    }
    if success:
        params["result_payload"] = event.get("tool_response")
    else:
        params["error_message"] = str(event.get("error") or "host tool failure")
        params["error_type"] = "HostToolFailure"
    result = await _call(socket_path, "action.postflight", params)
    if result.get("recorded") is not True:
        raise HookControlError("control_unavailable")
    return HookAdapterResponse(exit_code=0)


async def handle_host_hook(
    host: str,
    socket_path: str,
    event: Mapping[str, Any],
) -> HookAdapterResponse:
    """Translate one supported host event to the local hook-control protocol."""
    if host not in _HOST_NAMES or not Path(socket_path).is_absolute():
        return _failure()
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
            if host != "claude-code" and event_name == "PostToolUseFailure":
                return _failure()
            return await _handle_posttool(host, socket_path, event)
        return _failure()
    except (asyncio.TimeoutError, HookControlError, ValueError, TypeError):
        return _failure()
    except Exception:
        # A hook command must never surface internal errors or fail open.
        return _failure()


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
    parser = argparse.ArgumentParser(description="Atellagent external-agent hook adapter")
    parser.add_argument("--host", choices=sorted(_HOST_NAMES), required=True)
    parser.add_argument("--socket", required=True, help="absolute local hook-control socket")
    args = parser.parse_args(argv)
    try:
        response = asyncio.run(handle_host_hook(args.host, args.socket, _parse_stdin()))
    except (ValueError, TypeError, json.JSONDecodeError):
        response = _failure()
    if response.stdout:
        print(response.stdout)
    if response.stderr:
        print(response.stderr, file=sys.stderr)
    raise SystemExit(response.exit_code)


__all__ = [
    "HookAdapterResponse",
    "INTERNAL_DEADLINE_SECONDS",
    "handle_host_hook",
    "host_hook_capabilities",
    "main",
]
