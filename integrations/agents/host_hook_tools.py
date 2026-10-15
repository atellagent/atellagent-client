# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Prompt and tool event adapters for Codex and Claude Code."""

from typing import Any, Mapping
from uuid import UUID
from atellagent_client.pep.contracts import raw_action_fingerprint
from .hook_control import HookControlError
from .host_hook_contracts import HookAdapterResponse, HOST_HOOK_ADAPTER_VERSION
from .host_hook_events import (
    _string,
    _event,
    _turn_id,
    _tool_fields,
    _arguments,
    _allowed,
    _json,
    _bounded_postflight_result,
    _host_action_context,
)
from .host_hook_responses import _prompt_result, _pretool_result
from . import host_hook_transport


async def _handle_prompt(
    host: str, socket_path: str, event: Mapping[str, Any]
) -> HookAdapterResponse:
    _event(event, ("UserPromptSubmit",))
    prompt = event.get("prompt", event.get("user_prompt"))
    if not isinstance(prompt, str) or not prompt.strip():
        raise HookControlError("hook_input_invalid")
    session_id = _string(event, "session_id")
    turn_id = _turn_id(host, event, fallback=prompt)
    result = await host_hook_transport._call(
        socket_path,
        "model.decision",
        {
            "host": host,
            "session_id": session_id,
            "turn_id": turn_id,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    return _prompt_result(host, _allowed(result))


async def _handle_pretool(
    host: str, socket_path: str, event: Mapping[str, Any]
) -> HookAdapterResponse:
    _event(event, ("PreToolUse",))
    session_id, turn_id, tool_call_id, tool_name = _tool_fields(host, event)
    host_cwd = _host_action_context(host, event).get("host_cwd")
    result = await host_hook_transport._call(
        socket_path,
        "action.preflight",
        {
            "host": host,
            "session_id": session_id,
            "turn_id": turn_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": _arguments(event.get("tool_input")),
            "adapter_version": HOST_HOOK_ADAPTER_VERSION,
            "postflight_required": host != "gemini-cli",
            **({"host_cwd": host_cwd} if host_cwd is not None else {}),
        },
    )
    reference = result.get("assessment_reference")
    if host == "codex" and _allowed(result) and reference is not None:
        arguments = _arguments(event.get("tool_input"))
        if not tool_name.startswith("mcp__") or "_atellagent_check" in arguments:
            raise HookControlError("control_response_invalid")
        try:
            reference = str(UUID(reference))
        except (ValueError, TypeError, AttributeError) as exc:
            raise HookControlError("control_response_invalid") from exc
        return HookAdapterResponse(
            exit_code=0,
            stdout=_json(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "updatedInput": {**arguments, "_atellagent_check": reference},
                    },
                }
            ),
        )
    return _pretool_result(host, _allowed(result), result.get("reason_code"))


async def _handle_posttool(
    host: str, socket_path: str, event: Mapping[str, Any]
) -> HookAdapterResponse:
    event_name = _event(event, ("PostToolUse", "PostToolUseFailure"))
    session_id, turn_id, tool_call_id, tool_name = _tool_fields(host, event)
    if "tool_input" not in event:
        raise HookControlError("outcome_binding_mismatch")
    observed_fingerprint = raw_action_fingerprint(
        action=tool_name, facts=_arguments(event["tool_input"]),
        raw_context=_host_action_context(host, event),
    )
    params: dict[str, Any] = {
        "host": host,
        "session_id": session_id,
        "turn_id": turn_id,
        "tool_call_id": tool_call_id,
        "hook_event_name": event_name,
        "observed_action_fingerprint": observed_fingerprint,
        "success": event_name == "PostToolUse",
    }
    if host == "codex":
        params["success"] = None
        params["outcome_observation"] = "result_observed"
        params["result_payload"] = _bounded_postflight_result(
            event.get("tool_response")
        )
    elif params["success"]:
        params["result_payload"] = _bounded_postflight_result(
            event.get("tool_response")
        )
    elif params["success"] is False:
        params["error_message"] = str(event.get("error") or "host tool failure")
        params["error_type"] = "HostToolFailure"
    result = await host_hook_transport._call(socket_path, "action.postflight", params)
    if result.get("recorded") is not True:
        raise HookControlError("control_response_invalid")
    return HookAdapterResponse(exit_code=0)
