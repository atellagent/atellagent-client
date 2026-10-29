# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Owner-only Windows named-pipe transport for local hook control.

This module deliberately imports pywin32 only from Windows-only call paths so
the portable client remains importable on POSIX hosts. The pipe DACL grants the
current Windows user SID only and rejects remote clients.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from threading import Event, Thread
from time import monotonic
from typing import Any, Mapping, Optional

from atellagent_client.hook_limits import MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES


WINDOWS_NAMED_PIPE_PREFIX = "\\\\.\\pipe\\"
_PIPE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# These are Win32 CreateNamedPipe flags. They are defined here rather than
# depending on the set of constants exported by a particular pywin32 build.
_FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008


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


def windows_local_state_path(endpoint: str) -> Path:
    """Return a user-private durable-state location for one pipe endpoint."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise WindowsNamedPipeError("windows_local_appdata_unavailable")
    directory = Path(local_app_data) / "Atellagent" / "hook-control"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _secure_user_directory(directory)
    suffix = sha256(validate_windows_named_pipe(endpoint).encode("utf-8")).hexdigest()[:24]
    return directory / f"{suffix}.outcomes.json"


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


def _current_user_security_descriptor() -> Any:
    """Build an inheritable DACL granting only the current user full control."""

    pywintypes, win32api, win32con, _win32file, _win32pipe, win32security, _os = _windows_modules()
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    dacl = win32security.ACL()
    inheritance = (
        win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
    )
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION,
        inheritance,
        win32con.GENERIC_ALL,
        user_sid,
    )
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    return descriptor, attributes


def _secure_user_directory(directory: Path) -> None:
    """Apply the same current-user DACL to local outbox state."""

    _pywintypes, _win32api, win32con, _win32file, _win32pipe, win32security, _os = _windows_modules()
    descriptor, _attributes = _current_user_security_descriptor()
    try:
        win32security.SetFileSecurity(
            str(directory), win32security.DACL_SECURITY_INFORMATION, descriptor
        )
    except Exception as exc:
        raise WindowsNamedPipeError("windows_local_state_acl_unavailable") from exc


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


class WindowsNamedPipeServer:
    """One-request-per-connection JSON-lines server on an owner-only pipe."""

    def __init__(
        self,
        endpoint: str,
        handler: Callable[[bytes], Awaitable[Mapping[str, Any]]],
        *,
        handler_timeout_seconds: float,
    ) -> None:
        self.endpoint = validate_windows_named_pipe(endpoint)
        self._handler = handler
        self._handler_timeout_seconds = max(0.1, float(handler_timeout_seconds))
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[Thread] = None
        self._active_handler_future: Optional[Any] = None
        self._stopped = Event()
        self._started = Event()
        self._start_error: Optional[WindowsNamedPipeError] = None

    async def start(self) -> None:
        if self._thread is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._thread = Thread(
            target=self._serve, name="atellagent-hook-control-pipe", daemon=True
        )
        self._thread.start()
        await asyncio.to_thread(self._started.wait)
        if self._start_error is not None:
            thread, self._thread = self._thread, None
            if thread is not None:
                await asyncio.to_thread(thread.join)
            raise self._start_error

    def close(self) -> None:
        self._stopped.set()
        future = self._active_handler_future
        if future is not None:
            future.cancel()
        self._wake_listener()

    async def wait_closed(self) -> None:
        thread, self._thread = self._thread, None
        if thread is not None:
            await asyncio.to_thread(thread.join)

    def _new_pipe(self, *, first_instance: bool) -> Any:
        _pywintypes, _win32api, win32con, _win32file, win32pipe, _win32security, _os = _windows_modules()
        _descriptor, attributes = _current_user_security_descriptor()
        open_mode = win32pipe.PIPE_ACCESS_DUPLEX
        if first_instance:
            open_mode |= _FILE_FLAG_FIRST_PIPE_INSTANCE
        pipe_mode = (
            win32pipe.PIPE_TYPE_MESSAGE
            | win32pipe.PIPE_READMODE_MESSAGE
            | win32pipe.PIPE_WAIT
            | _PIPE_REJECT_REMOTE_CLIENTS
        )
        return win32pipe.CreateNamedPipe(
            self.endpoint,
            open_mode,
            pipe_mode,
            1,
            MAX_RESPONSE_BYTES + 1,
            MAX_REQUEST_BYTES + 1,
            0,
            attributes,
        )

    def _serve(self) -> None:
        try:
            # Keep the first pipe instance open for the server lifetime. If we
            # closed and recreated it between requests, another same-user
            # process could claim the name before the next instance existed.
            handle = self._new_pipe(first_instance=True)
            self._started.set()
            try:
                while not self._stopped.is_set():
                    self._serve_connection(handle)
                    if not self._stopped.is_set():
                        self._disconnect(handle)
            finally:
                self._disconnect_and_close(handle)
        except WindowsNamedPipeError as exc:
            self._start_error = exc
            self._started.set()
        except Exception:
            self._start_error = WindowsNamedPipeError("windows_named_pipe_unavailable")
            self._started.set()

    def _serve_connection(self, handle: Any) -> None:
        pywintypes, _win32api, _win32con, win32file, win32pipe, _win32security, _os = _windows_modules()
        try:
            win32pipe.ConnectNamedPipe(handle, None)
        except pywintypes.error as exc:
            if getattr(exc, "winerror", None) != 535:  # ERROR_PIPE_CONNECTED
                return
        if self._stopped.is_set():
            return
        try:
            _status, request = win32file.ReadFile(handle, MAX_REQUEST_BYTES + 1)
        except pywintypes.error:
            request = b""
        if self._stopped.is_set() or self._loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._handler(bytes(request)), self._loop)
        self._active_handler_future = future
        try:
            response = future.result(timeout=self._handler_timeout_seconds)
        except Exception:
            response = {
                "id": None,
                "ok": False,
                "error": {"code": "control_unavailable"},
            }
        finally:
            self._active_handler_future = None
        frame = json.dumps(dict(response), separators=(",", ":")).encode("utf-8") + b"\n"
        try:
            win32file.WriteFile(handle, frame)
        except pywintypes.error:
            return

    @staticmethod
    def _disconnect(handle: Any) -> None:
        _pywintypes, _win32api, _win32con, win32file, win32pipe, _win32security, _os = _windows_modules()
        try:
            win32pipe.DisconnectNamedPipe(handle)
        except Exception:
            pass

    @classmethod
    def _disconnect_and_close(cls, handle: Any) -> None:
        _pywintypes, _win32api, _win32con, win32file, _win32pipe, _win32security, _os = _windows_modules()
        cls._disconnect(handle)
        win32file.CloseHandle(handle)

    def _wake_listener(self) -> None:
        try:
            _windows_named_pipe_request(self.endpoint, b"", timeout_seconds=0.1)
        except WindowsNamedPipeError:
            return


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
    "WindowsNamedPipeServer",
    "validate_windows_named_pipe",
    "windows_local_state_path",
    "windows_named_pipe_request",
]
