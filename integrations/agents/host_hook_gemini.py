# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Gemini CLI before-model and before-tool adapters."""

import json
from hashlib import sha256
from typing import Any, Mapping
from .hook_control import HookControlError
from .host_hook_contracts import HookAdapterResponse, HOST_HOOK_ADAPTER_VERSION
from .host_hook_events import _string, _event, _allowed
from .host_hook_responses import _prompt_result, _pretool_result
from . import host_hook_transport


def _gemini_turn_id(event: Mapping[str, Any], *, fallback: str) -> str:
    timestamp = _string(event, "timestamp")
    material = (
        f"gemini-cli:{_string(event, 'session_id')}:{timestamp}:{fallback}".encode(
            "utf-8"
        )
    )
    return f"hook-{sha256(material).hexdigest()}"


async def _handle_gemini_before_model(
    socket_path: str, event: Mapping[str, Any]
) -> HookAdapterResponse:
    _event(event, ("BeforeModel",))
    request = event.get("llm_request")
    if (
        not isinstance(request, Mapping)
        or not isinstance(request.get("messages"), list)
        or not isinstance(request.get("model"), str)
        or not str(request["model"]).strip()
    ):
        raise HookControlError("hook_input_invalid")
    session_id = _string(event, "session_id")
    messages: list[dict[str, Any]] = []
    for message in request["messages"]:
        if not isinstance(message, Mapping):
            raise HookControlError("hook_input_invalid")
        normalized = dict(message)
        if normalized.get("role") == "model":
            normalized["role"] = "assistant"
        messages.append(normalized)
    provider_request = {
        key: request[key] for key in ("config", "toolConfig") if key in request
    }
    turn_id = _gemini_turn_id(
        event, fallback=json.dumps(messages, sort_keys=True, separators=(",", ":"))
    )
    result = await host_hook_transport._call(
        socket_path,
        "model.decision",
        {
            "host": "gemini_cli",
            "session_id": session_id,
            "turn_id": turn_id,
            "input_scope": "full_model_request",
            "messages": messages,
            "model": str(request["model"]).strip(),
            "provider": "google",
            "provider_request": provider_request,
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
        raise HookControlError("hook_input_invalid")
    tool_call_id = _gemini_turn_id(
        event,
        fallback=f"{tool_name}:{json.dumps(dict(arguments), sort_keys=True, separators=(',', ':'))}",
    )
    result = await host_hook_transport._call(
        socket_path,
        "action.preflight",
        {
            "host": "gemini_cli",
            "session_id": session_id,
            "turn_id": tool_call_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": dict(arguments),
            "adapter_version": HOST_HOOK_ADAPTER_VERSION,
            "postflight_required": False,
        },
    )
    return _pretool_result("gemini-cli", _allowed(result), result.get("reason_code"))
