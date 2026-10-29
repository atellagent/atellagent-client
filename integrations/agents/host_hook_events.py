# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Normalize host events and bound postflight evidence."""

import json
from hashlib import sha256
from typing import Any, Mapping, Sequence
from .hook_control import HookControlError
from .host_hook_contracts import HOST_HOOK_ADAPTER_VERSION

_MAX_POSTFLIGHT_RESULT_BYTES = 16 * 1024


def _string(event: Mapping[str, Any], field: str) -> str:
    value = event.get(field)
    if not isinstance(value, str) or not value.strip():
        raise HookControlError("hook_input_invalid")
    return value.strip()


def _event(event: Mapping[str, Any], expected: Sequence[str]) -> str:
    event_name = _string(event, "hook_event_name")
    if event_name not in expected:
        raise HookControlError("hook_input_invalid")
    return event_name


def _turn_id(host: str, event: Mapping[str, Any], *, fallback: str) -> str:
    supplied = event.get("turn_id")
    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip()
    material = f"{host}:{_string(event, 'session_id')}:{fallback}".encode("utf-8")
    return f"hook-{sha256(material).hexdigest()}"


def _arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    # The Codex schema permits any JSON value for tool_input. The daemon's
    # public protocol intentionally uses object facts, so preserve such input
    # under one neutral field instead of interpreting it locally.
    return {"input": value}


def _allowed(result: Mapping[str, Any]) -> bool:
    value = result.get("allowed")
    if not isinstance(value, bool):
        raise HookControlError("control_response_invalid")
    return value


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _bounded_postflight_result(value: Any) -> Any:
    """Keep large host results out of the local control protocol and evidence."""

    try:
        encoded = json.dumps(
            value,
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return {"result_unavailable": True}
    if len(encoded) <= _MAX_POSTFLIGHT_RESULT_BYTES:
        return value
    return {
        "result_truncated": True,
        "result_byte_length": len(encoded),
        "result_sha256": sha256(encoded).hexdigest(),
    }


def _tool_fields(host: str, event: Mapping[str, Any]) -> tuple[str, str, str, str]:
    session_id = _string(event, "session_id")
    tool_call_id = _string(event, "tool_use_id")
    tool_name = _string(event, "tool_name")
    return (
        session_id,
        _turn_id(host, event, fallback=tool_call_id),
        tool_call_id,
        tool_name,
    )


def _host_action_context(host: str, event: Mapping[str, Any]) -> dict[str, str]:
    """Use the same raw context at admission and host completion."""
    context = {"host": host, "adapter_version": HOST_HOOK_ADAPTER_VERSION}
    cwd = event.get("cwd") if host == "codex" else None
    if cwd is not None:
        if (not isinstance(cwd, str) or not cwd.strip() or "\x00" in cwd
                or len(cwd.encode("utf-8", errors="surrogatepass")) > 4096):
            raise HookControlError("hook_input_invalid")
        context["host_cwd"] = cwd
    return context
