# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Owner-private socket and named-pipe lifecycle for local hook RPCs."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import errno
import json
import logging
import os
from pathlib import Path
import stat
from time import monotonic
from typing import Any, Dict

if os.name != "nt":
    import fcntl

from atellagent_client.governance import ActionDenied
from atellagent_client.sdk.errors import PolicyViolationError

from .hook_control_helpers import (
    _controller_diagnostic_code, _object, _request_correlation, _safe_error_code,
    _safe_http_status, _safe_request_id, _safe_gateway_failure_code,
)
from .hook_control_protocol import HOOK_CONTROL_PROTOCOL, MAX_REQUEST_BYTES, HookControlError
from .windows_named_pipe import WindowsNamedPipeError, WindowsNamedPipeServer

_LOGGER = logging.getLogger("atellagent_client.integrations.agents.hook_control")
_TIMING_LOG_ENABLED = os.environ.get("ATELLAGENT_HOOK_TIMING_LOG") == "1"


class HookControlTransportMixin:
    """Transport lifecycle; the runtime owns policy state and RPC dispatch."""

    def _ensure_socket_parent(self) -> None:
        parent = self.socket_path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent_stat = parent.stat()
        if parent_stat.st_uid != os.getuid() or stat.S_IMODE(parent_stat.st_mode) & 0o077:
            raise HookControlError("socket_parent_not_private")
        if self.socket_path.exists() or self.socket_path.is_symlink():
            socket_stat = self.socket_path.lstat()
            if socket_stat.st_uid != os.getuid() or not stat.S_ISSOCK(socket_stat.st_mode):
                raise HookControlError("socket_path_unsafe")

    @property
    def _socket_lock_path(self) -> Path:
        return self.socket_path.with_name(f"{self.socket_path.name}.lock")

    def _acquire_socket_lock(self) -> None:
        """Claim exclusive ownership before removing a stale socket pathname."""

        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self._socket_lock_path, flags, 0o600)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EACCES, errno.EPERM}:
                raise HookControlError("socket_path_unsafe") from exc
            raise
        try:
            lock_stat = os.fstat(fd)
            if lock_stat.st_uid != os.getuid() or not stat.S_ISREG(lock_stat.st_mode):
                raise HookControlError("socket_path_unsafe")
            os.chmod(self._socket_lock_path, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise HookControlError("control_runtime_already_running") from exc
            raise
        except Exception:
            os.close(fd)
            raise
        self._socket_lock_fd = fd

    def _release_socket_lock(self) -> None:
        fd, self._socket_lock_fd = self._socket_lock_fd, None
        if fd is None:
            return
        with suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with suppress(OSError):
            os.close(fd)

    def _remove_stale_socket(self) -> None:
        if self.socket_path.exists() or self.socket_path.is_symlink():
            socket_stat = self.socket_path.lstat()
            if socket_stat.st_uid != os.getuid() or not stat.S_ISSOCK(socket_stat.st_mode):
                raise HookControlError("socket_path_unsafe")
            self.socket_path.unlink()

    def _owns_socket_path(self) -> bool:
        if self._socket_identity is None:
            return False
        try:
            socket_stat = self.socket_path.lstat()
        except FileNotFoundError:
            return False
        return stat.S_ISSOCK(socket_stat.st_mode) and (
            socket_stat.st_dev,
            socket_stat.st_ino,
        ) == self._socket_identity

    async def start(self) -> None:
        if self.started:
            return
        if self._uses_windows_named_pipe:
            await self._start_windows_named_pipe()
            return
        self._ensure_socket_parent()
        self._acquire_socket_lock()
        try:
            self._remove_stale_socket()
            await self._outbox.load()
            self._bind_native_participant()
            await self.participant.start()
            self._server = await asyncio.start_unix_server(
                self._handle_connection,
                path=str(self.socket_path),
                limit=MAX_REQUEST_BYTES + 1,
            )
            os.chmod(self.socket_path, 0o600)
            socket_stat = self.socket_path.lstat()
            if socket_stat.st_uid != os.getuid() or not stat.S_ISSOCK(socket_stat.st_mode):
                raise HookControlError("socket_path_unsafe")
            self._socket_identity = (socket_stat.st_dev, socket_stat.st_ino)
            self._stop_event.clear()
            self._outbox_task = asyncio.create_task(
                self._run_outbox_delivery(), name="hook-outcome-delivery"
            )
            self._outbox_maintenance_task = asyncio.create_task(
                self._run_outbox_maintenance(), name="hook-outcome-maintenance"
            )
        except Exception:
            server, self._server = self._server, None
            if server is not None:
                server.close()
                await server.wait_closed()
            self._socket_identity = None
            await self.participant.stop()
            self._release_socket_lock()
            raise

    async def _start_windows_named_pipe(self) -> None:
        """Start the ACL-protected Windows control pipe after authorization."""

        try:
            self.startup_stage = "outbox"
            await self._outbox.load()
            self.startup_stage = "native_binding"
            self._bind_native_participant()
            self.startup_stage = "participant"
            await self.participant.start()
            self.startup_stage = "control_pipe"
            self._windows_server = WindowsNamedPipeServer(
                str(self.socket_path),
                self._response_for,
                handler_timeout_seconds=max(
                    self.rpc_timeout_seconds, self.mcp_rpc_timeout_seconds
                )
                + 1.0,
            )
            await self._windows_server.start()
            self.startup_stage = "ready"
            self._stop_event.clear()
            self._outbox_task = asyncio.create_task(
                self._run_outbox_delivery(), name="hook-outcome-delivery"
            )
            self._outbox_maintenance_task = asyncio.create_task(
                self._run_outbox_maintenance(), name="hook-outcome-maintenance"
            )
        except WindowsNamedPipeError as exc:
            await self._stop_windows_named_pipe()
            raise HookControlError(exc.code) from exc
        except Exception:
            await self._stop_windows_named_pipe()
            raise

    async def run_forever(self) -> None:
        await self.start()
        await self._stop_event.wait()

    async def stop(self) -> None:
        if self._uses_windows_named_pipe:
            await self._stop_windows_named_pipe()
            return
        self._stop_event.set()
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        outbox_task, self._outbox_task = self._outbox_task, None
        if outbox_task is not None:
            outbox_task.cancel()
            await asyncio.gather(outbox_task, return_exceptions=True)
        maintenance_task, self._outbox_maintenance_task = self._outbox_maintenance_task, None
        if maintenance_task is not None:
            maintenance_task.cancel()
            await asyncio.gather(maintenance_task, return_exceptions=True)
        try:
            if self._owns_socket_path():
                self.socket_path.unlink()
        finally:
            self._socket_identity = None
            try:
                await self.participant.stop()
            finally:
                self._release_socket_lock()

    async def _stop_windows_named_pipe(self) -> None:
        """Stop the pipe without manipulating POSIX socket state or locks."""

        self._stop_event.set()
        server, self._windows_server = self._windows_server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        outbox_task, self._outbox_task = self._outbox_task, None
        if outbox_task is not None:
            outbox_task.cancel()
            await asyncio.gather(outbox_task, return_exceptions=True)
        maintenance_task, self._outbox_maintenance_task = self._outbox_maintenance_task, None
        if maintenance_task is not None:
            maintenance_task.cancel()
            await asyncio.gather(maintenance_task, return_exceptions=True)
        self._socket_identity = None
        await self.participant.stop()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            line = await reader.readline()
            response = await self._response_for(line)
            writer.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
            await writer.drain()
        except Exception:
            return
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _response_for(self, line: bytes) -> Dict[str, Any]:
        request_id: Any = None
        method = "invalid_request"
        params: Any = None
        outcome = "failed"
        started_at = monotonic()
        try:
            if not line or len(line) > MAX_REQUEST_BYTES:
                raise HookControlError("invalid_request")
            decoded = json.loads(line)
            request = _object(
                decoded,
                "request",
                allowed={"protocol_version", "id", "method", "params"},
            )
            request_id = request.get("id")
            if request.get("protocol_version") != HOOK_CONTROL_PROTOCOL:
                raise HookControlError("unsupported_protocol")
            if not isinstance(request_id, str) or not request_id:
                raise HookControlError("invalid_request_id")
            method = str(request.get("method") or "").strip()
            params = request.get("params")
            timeout_seconds = (
                self.mcp_rpc_timeout_seconds
                if method == "mcp.invoke"
                else 45.0
                if method == "auth.reload"
                else self.rpc_timeout_seconds
            )
            result = await asyncio.wait_for(
                self._dispatch(method, params),
                timeout=timeout_seconds,
            )
            outcome = "completed"
            return {"id": request_id, "ok": True, "result": result}
        except asyncio.TimeoutError:
            outcome = "timed_out"
            _LOGGER.warning(
                "hook_control_request_failed method=%s code=control_timeout correlation=%s elapsed_ms=%s",
                method, _request_correlation(params), round((monotonic() - started_at) * 1000),
            )
            return {"id": request_id, "ok": False, "error": {"code": "control_timeout"}}
        except Exception as exc:
            code = _safe_error_code(exc)
            diagnostic = _controller_diagnostic_code(exc)
            if diagnostic is not None:
                self._last_controller_diagnostic = diagnostic
            is_policy_denial = isinstance(exc, (PolicyViolationError, ActionDenied))
            (_LOGGER.info if is_policy_denial else _LOGGER.warning)(
                "hook_control_request_%s method=%s code=%s correlation=%s elapsed_ms=%s http_status=%s request_id=%s gateway_failure_code=%s exception_type=%s",
                "denied" if is_policy_denial else "failed",
                method, code, _request_correlation(params), round((monotonic() - started_at) * 1000),
                _safe_http_status(exc),
                _safe_request_id(exc),
                _safe_gateway_failure_code(exc),
                type(exc).__name__ if code == "control_unavailable" else "none",
            )
            return {"id": request_id, "ok": False, "error": {"code": code}}
        finally:
            if outcome == "completed" and _TIMING_LOG_ENABLED:
                _LOGGER.info(
                    "hook_control_request_completed method=%s correlation=%s elapsed_ms=%s",
                    method,
                    _request_correlation(params),
                    round((monotonic() - started_at) * 1_000),
                )
            self._record_event_health(
                method=method,
                params=params,
                outcome=outcome,
                elapsed_ms=round((monotonic() - started_at) * 1_000),
            )
