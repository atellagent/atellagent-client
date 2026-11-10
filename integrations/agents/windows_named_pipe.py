# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Owner-only Windows named-pipe transport for local hook control.

This module deliberately imports pywin32 only from Windows-only call paths so
the portable client remains importable on POSIX hosts. The pipe DACL grants the
current Windows user SID only and rejects remote clients.
"""

from __future__ import annotations

import asyncio
import os
import re
from time import monotonic
from typing import Any

from atellagent_client.hook_limits import MAX_RESPONSE_BYTES


WINDOWS_NAMED_PIPE_PREFIX = "\\\\.\\pipe\\"
_PIPE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class WindowsNamedPipeError(RuntimeError):
    """A stable, non-sensitive Windows local-transport failure."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "control_socket_unavailable")
        super().__init__(self.code)


def validate_windows_named_pipe(endpoint: str) -> str:
    """Require one local, non-hierarchical named-pipe endpoint."""

    candidate = str(endpoint or "").strip()
    if not candidate.startswith(WINDOWS_NAMED_PIPE_PREFIX):
        raise ValueError("hook control endpoint must be a local Windows named pipe")
    name = candidate[len(WINDOWS_NAMED_PIPE_PREFIX) :]
    if not _PIPE_NAME.fullmatch(name):
        raise ValueError("Windows named-pipe name is invalid")
    return candidate


def _windows_modules() -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    if os.name != "nt":
        raise WindowsNamedPipeError("windows_named_pipe_unsupported_platform")
    try:
        import pywintypes
        import win32api
        import win32con
        import win32file
        import win32pipe
        import win32security
    except ImportError as exc:
        raise WindowsNamedPipeError("windows_named_pipe_support_unavailable") from exc
    return pywintypes, win32api, win32con, win32file, win32pipe, win32security, os


def _pipe_error_code(exc: BaseException) -> str:
    """Map Win32 errors without leaking local pipe or filesystem detail."""

    code = getattr(exc, "winerror", None)
    if code in {2, 3}:  # ERROR_FILE_NOT_FOUND / ERROR_PATH_NOT_FOUND
        return "control_socket_missing"
    if code == 5:  # ERROR_ACCESS_DENIED
        return "control_socket_access_denied"
    if code in {109, 231, 233}:  # broken, busy, no process
        return "control_socket_refused"
    return "control_socket_unavailable"


def _windows_named_pipe_request(endpoint: str, frame: bytes, *, timeout_seconds: float) -> bytes:
    """Perform one bounded client request over the owner-only local pipe."""

    pywintypes, _win32api, win32con, win32file, win32pipe, _win32security, _os = _windows_modules()
    endpoint = validate_windows_named_pipe(endpoint)
    deadline = monotonic() + max(0.1, float(timeout_seconds))
    handle = None
    try:
        while True:
            try:
                handle = win32file.CreateFile(
                    endpoint,
                    win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                    0,
                    None,
                    win32con.OPEN_EXISTING,
                    0,
                    None,
                )
                break
            except pywintypes.error as exc:
                if getattr(exc, "winerror", None) != 231:  # ERROR_PIPE_BUSY
                    raise WindowsNamedPipeError(_pipe_error_code(exc)) from exc
                remaining_ms = int((deadline - monotonic()) * 1000)
                if remaining_ms <= 0:
                    raise WindowsNamedPipeError("control_socket_timeout") from exc
                try:
                    win32pipe.WaitNamedPipe(endpoint, remaining_ms)
                except pywintypes.error as wait_exc:
                    raise WindowsNamedPipeError(_pipe_error_code(wait_exc)) from wait_exc
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        win32file.WriteFile(handle, frame)
        _status, response = win32file.ReadFile(handle, MAX_RESPONSE_BYTES + 1)
        return bytes(response)
    except WindowsNamedPipeError:
        raise
    except pywintypes.error as exc:
        raise WindowsNamedPipeError(_pipe_error_code(exc)) from exc
    finally:
        if handle is not None:
            win32file.CloseHandle(handle)


async def windows_named_pipe_request(endpoint: str, frame: bytes, *, timeout_seconds: float) -> bytes:
    return await asyncio.to_thread(
        _windows_named_pipe_request, endpoint, frame, timeout_seconds=timeout_seconds
    )


__all__ = [
    "WINDOWS_NAMED_PIPE_PREFIX",
    "WindowsNamedPipeError",
    "validate_windows_named_pipe",
    "windows_named_pipe_request",
]
