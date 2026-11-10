# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Credential-free JSON-lines protocol used by local agent hooks."""

from __future__ import annotations

import asyncio
import errno
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, Mapping

from atellagent_client.hook_limits import MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES
from atellagent_client.governance import ActionDenied
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError


HOOK_CONTROL_PROTOCOL = "atellagent.hook-control.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")


def _is_windows() -> bool:
    return os.name == "nt"


class HookControlError(RuntimeError):
    """A safe, stable hook-control protocol error."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "control_unavailable")
        super().__init__(self.code)


def _local_transport_error_code(exc: BaseException) -> str:
    """Classify a local socket failure without exposing transport internals."""

    if isinstance(exc, asyncio.TimeoutError):
        return "control_socket_timeout"
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.startswith(("control_", "windows_")):
        return code
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        return "control_socket_missing"
    if isinstance(exc, PermissionError):
        return "control_socket_access_denied"
    if isinstance(exc, ConnectionRefusedError):
        return "control_socket_refused"
    if isinstance(exc, OSError):
        if exc.errno in {errno.EACCES, errno.EPERM}:
            return "control_socket_access_denied"
        if exc.errno in {errno.ENOENT, errno.ENOTDIR}:
            return "control_socket_missing"
        if exc.errno == errno.ECONNREFUSED:
            return "control_socket_refused"
    return "control_socket_unavailable"


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
    """Credential-free JSON-lines client for a local hook-control endpoint."""

    def __init__(self, socket_path: str, *, timeout_seconds: float = 19.0) -> None:
        if _is_windows():
            from .windows_named_pipe import validate_windows_named_pipe

            self.socket_path = validate_windows_named_pipe(socket_path)
        else:
            self.socket_path = str(Path(socket_path).expanduser())
        self.timeout_seconds = max(0.1, float(timeout_seconds))

    async def call(self, method: str, params: Mapping[str, Any]) -> Dict[str, Any]:
        request = {"protocol_version": HOOK_CONTROL_PROTOCOL, "id": "hook-request", "method": str(method or "").strip(), "params": dict(params)}
        frame = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(frame) > MAX_REQUEST_BYTES:
            raise HookControlError("control_request_too_large")
        writer = None
        try:
            # Connection, write and response consume one budget, not a fresh
            # timeout per operation. The host adapter reserves its own margin.
            async with asyncio.timeout(self.timeout_seconds):
                if _is_windows():
                    from .windows_named_pipe import windows_named_pipe_request

                    line = await windows_named_pipe_request(
                        self.socket_path, frame, timeout_seconds=self.timeout_seconds
                    )
                else:
                    reader, writer = await asyncio.open_unix_connection(
                        self.socket_path,
                        # StreamReader defaults to 64 KiB, which is smaller than
                        # the hook-control protocol's permitted response frame.
                        limit=MAX_RESPONSE_BYTES + 1,
                    )
                    writer.write(frame)
                    await writer.drain()
                    line = await reader.readline()
                    writer.close()
                    await writer.wait_closed()
        except Exception as exc:
            raise HookControlError(_local_transport_error_code(exc)) from exc
        finally:
            if writer is not None:
                writer.close()
        if not line or len(line) > MAX_RESPONSE_BYTES:
            raise HookControlError("control_response_invalid")
        try:
            response = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise HookControlError("control_response_invalid") from exc
        if not isinstance(response, Mapping) or response.get("id") != "hook-request":
            raise HookControlError("control_response_invalid")
        if response.get("ok") is not True:
            error = response.get("error")
            code = error.get("code") if isinstance(error, Mapping) else None
            raise HookControlError(str(code or "control_unavailable"))
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise HookControlError("control_response_invalid")
        return dict(result)


__all__ = ["HOOK_CONTROL_PROTOCOL", "MAX_REQUEST_BYTES", "MAX_RESPONSE_BYTES", "HookControlClient", "HookControlError", "identifier", "messages", "object_fields", "safe_error_code"]
