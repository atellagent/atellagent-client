# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Credential-free JSON-lines protocol used by local agent hooks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from typing import Any, Dict, Mapping

from atellagent_client.governance import ActionDenied
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError


HOOK_CONTROL_PROTOCOL = "atellagent.hook-control.v1"
MAX_REQUEST_BYTES = 64 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")


class HookControlError(RuntimeError):
    """A safe, stable hook-control protocol error."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "control_unavailable")
        super().__init__(self.code)


def identifier(value: Any, field_name: str) -> str:
    candidate = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(candidate):
        raise HookControlError(f"invalid_{field_name}")
    return candidate


def object_fields(value: Any, field_name: str, *, allowed: set[str]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HookControlError(f"invalid_{field_name}")
    result = dict(value)
    if set(result) - allowed:
        raise HookControlError(f"unsupported_{field_name}_field")
    return result


def messages(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise HookControlError("invalid_messages")
    result: list[Dict[str, Any]] = []
    for message in value:
        if not isinstance(message, Mapping):
            raise HookControlError("invalid_messages")
        normalized = dict(message)
        if normalized.get("role") not in {"system", "user", "assistant", "tool", "function"}:
            raise HookControlError("invalid_messages")
        if not isinstance(normalized.get("content"), str):
            raise HookControlError("invalid_messages")
        result.append(normalized)
    return result


def safe_error_code(exc: Exception, *, default: str = "control_unavailable") -> str:
    if isinstance(exc, HookControlError):
        return exc.code
    if isinstance(exc, PolicyViolationError):
        return str(exc.violation_type or "policy_denied")
    if isinstance(exc, ActionDenied):
        return exc.reason_code
    if isinstance(exc, PolicyTransportError):
        return "control_unavailable"
    return default


class HookControlClient:
    """Credential-free JSON-lines client for a local hook-control socket."""

    def __init__(self, socket_path: str, *, timeout_seconds: float = 5.0) -> None:
        self.socket_path = str(Path(socket_path).expanduser())
        self.timeout_seconds = max(0.1, float(timeout_seconds))

    async def call(self, method: str, params: Mapping[str, Any]) -> Dict[str, Any]:
        request = {"protocol_version": HOOK_CONTROL_PROTOCOL, "id": "hook-request", "method": str(method or "").strip(), "params": dict(params)}
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(self.socket_path), timeout=self.timeout_seconds)
            writer.write(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
            await asyncio.wait_for(writer.drain(), timeout=self.timeout_seconds)
            line = await asyncio.wait_for(reader.readline(), timeout=self.timeout_seconds)
            writer.close()
            await writer.wait_closed()
        except Exception as exc:
            raise HookControlError("control_unavailable") from exc
        if not line or len(line) > MAX_REQUEST_BYTES:
            raise HookControlError("control_unavailable")
        try:
            response = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise HookControlError("control_unavailable") from exc
        if not isinstance(response, Mapping) or response.get("id") != "hook-request":
            raise HookControlError("control_unavailable")
        if response.get("ok") is not True:
            error = response.get("error")
            code = error.get("code") if isinstance(error, Mapping) else None
            raise HookControlError(str(code or "control_unavailable"))
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise HookControlError("control_unavailable")
        return dict(result)


__all__ = ["HOOK_CONTROL_PROTOCOL", "MAX_REQUEST_BYTES", "HookControlClient", "HookControlError", "identifier", "messages", "object_fields", "safe_error_code"]
