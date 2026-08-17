# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Local Unix-socket control service for supported external-agent hooks.

The service is deliberately a narrow transport and policy-enforcement point.
It authenticates to Atellagent through its enrolled connected participant; hook
processes only receive safe allow/deny results and never receive credentials.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Dict, Mapping, Optional

from atellagent_client.connected import ConnectedParticipant
from atellagent_client.governance import ActionDenied
from atellagent_client.protocol.agent_contracts import (
    GovernanceCallContext,
    GovernanceReceipt,
    ModelDecisionRequest,
)
from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError

from .control import ExternalAgentGovernance


HOOK_CONTROL_PROTOCOL = "atellagent.hook-control.v1"
_MAX_REQUEST_BYTES = 64 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_HOST_CAPABILITY = "agent.control"


class HookControlError(RuntimeError):
    """A safe, stable hook-control protocol error."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "control_unavailable")
        super().__init__(self.code)


@dataclass(frozen=True)
class _PendingAction:
    context: GovernanceCallContext
    receipt: GovernanceReceipt
    success: Optional[bool] = None
    result_payload: Any = None
    error_message: Optional[str] = None
    error_type: Optional[str] = None


def _identifier(value: Any, field_name: str) -> str:
    candidate = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(candidate):
        raise HookControlError(f"invalid_{field_name}")
    return candidate


def _object(value: Any, field_name: str, *, allowed: set[str]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HookControlError(f"invalid_{field_name}")
    result = dict(value)
    if set(result) - allowed:
        raise HookControlError(f"unsupported_{field_name}_field")
    return result


def _messages(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise HookControlError("invalid_messages")
    messages: list[Dict[str, Any]] = []
    for message in value:
        if not isinstance(message, Mapping):
            raise HookControlError("invalid_messages")
        normalized = dict(message)
        role = normalized.get("role")
        if role not in {"system", "user", "assistant", "tool", "function"}:
            raise HookControlError("invalid_messages")
        if not isinstance(normalized.get("content"), str):
            raise HookControlError("invalid_messages")
        messages.append(normalized)
    return messages


def _safe_error_code(exc: Exception, *, default: str = "control_unavailable") -> str:
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
        request = {
            "protocol_version": HOOK_CONTROL_PROTOCOL,
            "id": "hook-request",
            "method": str(method or "").strip(),
            "params": dict(params),
        }
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path),
                timeout=self.timeout_seconds,
            )
            writer.write(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
            await asyncio.wait_for(writer.drain(), timeout=self.timeout_seconds)
            line = await asyncio.wait_for(reader.readline(), timeout=self.timeout_seconds)
            writer.close()
            await writer.wait_closed()
        except Exception as exc:
            raise HookControlError("control_unavailable") from exc
        if not line or len(line) > _MAX_REQUEST_BYTES:
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


class HookControlRuntime:
    """Run the enrolled boundary and its local hook-control socket together."""

    def __init__(
        self,
        config: ServiceAccountConfig,
        *,
        socket_path: str,
        participant: Optional[ConnectedParticipant] = None,
        rpc_timeout_seconds: float = 8.0,
        postflight_attempts: int = 3,
    ) -> None:
        if config.integration_type != "agent":
            raise ValueError("hook control requires a connected agent integration")
        if config.identity_mode != "boundary_identity_only":
            raise ValueError("hook control requires boundary_identity_only identity mode")
        if set(config.capabilities) != {_HOST_CAPABILITY}:
            raise ValueError("hook control requires only the provisioned agent.control capability")
        candidate = Path(socket_path).expanduser()
        if not candidate.is_absolute() or not candidate.name:
            raise ValueError("hook control socket_path must be an absolute path")
        self.config = config
        self.socket_path = candidate
        self.participant = participant or ConnectedParticipant(config)
        if self.participant.config is not config:
            raise ValueError("hook control participant must use the enrolled configuration")
        self.governance = ExternalAgentGovernance(
            config,
            session_provider=lambda: self.participant.session,
        )
        self.rpc_timeout_seconds = max(0.1, float(rpc_timeout_seconds))
        self.postflight_attempts = max(1, int(postflight_attempts))
        self._server: Optional[asyncio.AbstractServer] = None
        self._pending: Dict[str, _PendingAction] = {}
        self._reserving: set[str] = set()
        self._pending_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    @property
    def started(self) -> bool:
        return self._server is not None

    @staticmethod
    def _pending_key(*, host: str, session_id: str, turn_id: str, tool_call_id: str) -> str:
        return ":".join((host, session_id, turn_id, tool_call_id))

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
            self.socket_path.unlink()

    async def start(self) -> None:
        if self._server is not None:
            return
        self._ensure_socket_parent()
        await self.participant.start()
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_connection,
                path=str(self.socket_path),
            )
            os.chmod(self.socket_path, 0o600)
            self._stop_event.clear()
        except Exception:
            await self.participant.stop()
            raise

    async def run_forever(self) -> None:
        await self.start()
        await self._stop_event.wait()

    async def stop(self) -> None:
        self._stop_event.set()
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        try:
            if self.socket_path.exists() and stat.S_ISSOCK(self.socket_path.lstat().st_mode):
                self.socket_path.unlink()
        finally:
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
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _response_for(self, line: bytes) -> Dict[str, Any]:
        request_id: Any = None
        try:
            if not line or len(line) > _MAX_REQUEST_BYTES:
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
            result = await asyncio.wait_for(
                self._dispatch(method, params),
                timeout=self.rpc_timeout_seconds,
            )
            return {"id": request_id, "ok": True, "result": result}
        except asyncio.TimeoutError:
            return {"id": request_id, "ok": False, "error": {"code": "control_timeout"}}
        except Exception as exc:
            return {"id": request_id, "ok": False, "error": {"code": _safe_error_code(exc)}}

    async def _dispatch(self, method: str, raw_params: Any) -> Dict[str, Any]:
        if method == "health":
            if raw_params not in ({}, None):
                raise HookControlError("invalid_health_params")
            async with self._pending_lock:
                unresolved = sum(1 for pending in self._pending.values() if pending.success is not None)
            return {
                "protocol_version": HOOK_CONTROL_PROTOCOL,
                "capabilities": [_HOST_CAPABILITY],
                "unresolved_postflights": unresolved,
            }
        if method == "model.decision":
            return await self._model_decision(raw_params)
        if method == "action.preflight":
            return await self._preflight(raw_params)
        if method == "action.postflight":
            return await self._postflight(raw_params)
        raise HookControlError("unsupported_method")

    @staticmethod
    def _turn_fields(raw_params: Any, *, include_tool: bool) -> Dict[str, Any]:
        if not isinstance(raw_params, Mapping):
            raise HookControlError("invalid_params")
        params = dict(raw_params)
        fields = {
            "host": _identifier(params.get("host"), "host"),
            "session_id": _identifier(params.get("session_id"), "session_id"),
            "turn_id": _identifier(params.get("turn_id"), "turn_id"),
        }
        if include_tool:
            fields["tool_call_id"] = _identifier(params.get("tool_call_id"), "tool_call_id")
        return fields

    async def _model_decision(self, raw_params: Any) -> Dict[str, Any]:
        params = _object(
            raw_params,
            "params",
            allowed={
                "host",
                "session_id",
                "turn_id",
                "input_scope",
                "messages",
                "model",
                "provider",
                "provider_request",
            },
        )
        self._turn_fields(params, include_tool=False)
        input_scope = str(params.get("input_scope") or "turn_entry").strip()
        if input_scope not in {"turn_entry", "full_model_request"}:
            raise HookControlError("invalid_input_scope")
        provider_request = params.get("provider_request")
        if provider_request is not None and not isinstance(provider_request, Mapping):
            raise HookControlError("invalid_provider_request")
        decision_request = ModelDecisionRequest(
            input_scope=input_scope,  # type: ignore[arg-type]
            messages=_messages(params.get("messages")),
            model=(str(params["model"]) if params.get("model") is not None else None),
            provider=(
                str(params["provider"])
                if params.get("provider") is not None
                else None
            ),
            provider_request=(dict(provider_request) if provider_request else None),
        )
        decision = await self.governance.model_decision_async(decision_request)
        if (
            decision.input_scope != decision_request.input_scope
            or decision.request_fingerprint != decision_request.request_fingerprint
        ):
            raise HookControlError("decision_binding_invalid")
        if decision.obligations:
            raise HookControlError("unsupported_obligation")
        return {
            "allowed": decision.outcome == "allow",
            "enforcement": decision.enforcement,
            "input_scope": decision.input_scope,
            "reason_code": decision.reason_code,
            "reason": decision.reason,
            "decision_id": decision.decision_id,
            "correlation_id": decision.correlation_id,
        }

    async def _preflight(self, raw_params: Any) -> Dict[str, Any]:
        params = _object(
            raw_params,
            "params",
            allowed={
                "host", "session_id", "turn_id", "tool_call_id", "tool_name", "arguments",
                "postflight_required",
            },
        )
        fields = self._turn_fields(params, include_tool=True)
        tool_name = _identifier(params.get("tool_name"), "tool_name")
        arguments = params.get("arguments")
        if not isinstance(arguments, Mapping):
            raise HookControlError("invalid_arguments")
        postflight_required = params.get("postflight_required", True)
        if not isinstance(postflight_required, bool):
            raise HookControlError("invalid_postflight_required")
        pending_key = self._pending_key(**fields)
        async with self._pending_lock:
            if pending_key in self._pending or pending_key in self._reserving:
                raise HookControlError("duplicate_tool_call")
            self._reserving.add(pending_key)
        context = GovernanceCallContext(
            tool_name=tool_name,
            arguments=dict(arguments),
            task_type="host_tool",
            runtime_mode="hook",
            capabilities=[_HOST_CAPABILITY],
            tool_call_id=fields["tool_call_id"],
            action_key=pending_key,
            request_payload={
                "host": fields["host"],
                "session_id": fields["session_id"],
                "turn_id": fields["turn_id"],
            },
        )
        try:
            receipt = await self.governance.preflight_async(context)
            if not receipt.is_executable:
                raise HookControlError("policy_denied")
            if receipt.obligations:
                failed = _PendingAction(
                    context=context,
                    receipt=receipt,
                    success=False,
                    error_message="local hook protocol cannot fulfill the required obligation",
                    error_type="UnsupportedObligation",
                )
                async with self._pending_lock:
                    self._reserving.discard(pending_key)
                    self._pending[pending_key] = failed
                await self._deliver_postflight(pending_key, failed)
                raise HookControlError("unsupported_obligation")
            try:
                await self.governance.action_gate.enforce(
                    action=context.tool_name,
                    integration_type="agent",
                    correlation_id=receipt.action_key,
                    encoded_directive=receipt.control_directive,
                    facts=context.arguments,
                    workflow_context=receipt.workflow_context,
                )
            except Exception:
                failed = _PendingAction(
                    context=context,
                    receipt=receipt,
                    success=False,
                    error_message="local directive verification failed",
                    error_type="ActionDenied",
                )
                async with self._pending_lock:
                    self._reserving.discard(pending_key)
                    self._pending[pending_key] = failed
                await self._deliver_postflight(pending_key, failed)
                raise
            async with self._pending_lock:
                self._reserving.discard(pending_key)
                if postflight_required:
                    self._pending[pending_key] = _PendingAction(context=context, receipt=receipt)
        except PolicyViolationError as exc:
            async with self._pending_lock:
                self._reserving.discard(pending_key)
            return {
                "allowed": False,
                "reason_code": str(exc.violation_type or "policy_denied"),
            }
        except Exception:
            async with self._pending_lock:
                self._reserving.discard(pending_key)
            raise
        return {
            "allowed": True,
            "action_key": receipt.action_key,
            "decision_id": receipt.decision_id,
            "correlation_id": receipt.action_key,
        }

    async def _postflight(self, raw_params: Any) -> Dict[str, Any]:
        params = _object(
            raw_params,
            "params",
            allowed={
                "host", "session_id", "turn_id", "tool_call_id", "success",
                "result_payload", "error_message", "error_type",
            },
        )
        fields = self._turn_fields(params, include_tool=True)
        if not isinstance(params.get("success"), bool):
            raise HookControlError("invalid_success")
        pending_key = self._pending_key(**fields)
        async with self._pending_lock:
            pending = self._pending.get(pending_key)
            if pending is None:
                raise HookControlError("unknown_tool_call")
            success = bool(params["success"])
            if pending.success is not None and pending.success != success:
                raise HookControlError("postflight_conflict")
            if pending.success is None:
                pending = _PendingAction(
                    context=pending.context,
                    receipt=pending.receipt,
                    success=success,
                    result_payload=params.get("result_payload") if success else None,
                    error_message=str(params.get("error_message") or "").strip() or None,
                    error_type=str(params.get("error_type") or "").strip() or None,
                )
                self._pending[pending_key] = pending
        recorded = await self._deliver_postflight(pending_key, pending)
        if recorded:
            return {"recorded": True, "action_key": pending.receipt.action_key}
        return {"recorded": False, "reason_code": "postflight_unresolved"}

    async def _deliver_postflight(self, pending_key: str, pending: _PendingAction) -> bool:
        """Deliver one stored outcome with bounded retries and no local fallback."""
        for attempt in range(self.postflight_attempts):
            try:
                await self.governance.postflight_async(
                    pending.context,
                    receipt=pending.receipt,
                    result_payload=pending.result_payload,
                    success=bool(pending.success),
                    error_message=pending.error_message,
                    error_type=pending.error_type,
                )
            except Exception:
                if attempt + 1 < self.postflight_attempts:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                return False
            async with self._pending_lock:
                self._pending.pop(pending_key, None)
            return True
        return False  # pragma: no cover - non-empty attempt invariant


__all__ = [
    "HOOK_CONTROL_PROTOCOL",
    "HookControlClient",
    "HookControlError",
    "HookControlRuntime",
]
