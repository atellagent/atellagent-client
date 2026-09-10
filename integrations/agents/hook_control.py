# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Local Unix-socket control service for supported external-agent hooks.

The service is deliberately a narrow transport and policy-enforcement point.
It authenticates to Atellagent through its enrolled connected participant; hook
processes only receive safe allow/deny results and never receive credentials.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import errno
import fcntl
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
import stat
from time import monotonic
from typing import Any, Dict, Mapping, Optional

import httpx

from atellagent_client.connected import ConnectedParticipant
from atellagent_client.governance import ActionDenied
from atellagent_client.protocol.agent_contracts import (
    GovernanceCallContext,
    ModelDecisionRequest,
)
from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError
from atellagent_client.proxy.contracts import MCPProxyTool
from atellagent_client.sdk.client_modules.mcp_tools import _mcp_tool_result

from .control import ExternalAgentGovernance
from .hook_control_protocol import HookControlClient, HookControlError
from .hook_health import HookEventHealth
from .hook_outbox import HookOutcomeCorrelationUnavailable, HookOutcomeOutbox
from .native_hook_posture import NativeHookPostureCache


HOOK_CONTROL_PROTOCOL = "atellagent.hook-control.v1"
_MAX_REQUEST_BYTES = 64 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_HOST_CAPABILITY = "agent.control"
_MCP_RPC_TIMEOUT_SECONDS = 30.0
_PREFLIGHT_TIMEOUT_SECONDS = 4.0
_DIRECTIVE_VERIFICATION_TIMEOUT_SECONDS = 2.0
_OUTBOX_RETRY_INTERVAL_SECONDS = 5.0
_MODEL_DECISION_FAILURE_CODES = frozenset(
    {
        "model_decision_gateway_auth",
        "model_decision_gateway_client_status",
        "model_decision_gateway_response_invalid",
        "model_decision_gateway_server_status",
        "model_decision_gateway_status",
        "model_decision_gateway_transport",
    }
)
_LOGGER = logging.getLogger(__name__)


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
        safe_code = getattr(exc, "safe_code", None)
        if safe_code == "control_gateway_server_status":
            return safe_code
        if safe_code in _MODEL_DECISION_FAILURE_CODES:
            return safe_code
        return "model_decision_transport_failure"
    return default


def _safe_outcome_delivery_error_code(exc: Exception) -> str:
    """Classify retry failures without retaining endpoint responses or action data."""

    if isinstance(exc, asyncio.TimeoutError):
        return "outcome_delivery_timeout"
    if isinstance(exc, PolicyViolationError):
        return "outcome_delivery_policy_rejected"
    if isinstance(exc, PolicyTransportError):
        if getattr(exc, "safe_code", None) == "control_gateway_server_status":
            return "outcome_delivery_gateway_server_status"
        return "outcome_delivery_transport"
    if isinstance(exc, httpx.TransportError):
        return "outcome_delivery_transport"
    return "outcome_delivery_gateway_client_status"


def _request_correlation(params: Any) -> str:
    """Correlate failures without logging host content or raw identifiers."""
    if not isinstance(params, Mapping):
        return "unavailable"
    fields = [params.get(key) for key in ("host", "session_id", "turn_id", "tool_call_id")]
    if all(isinstance(value, str) and value for value in fields):
        return sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()[:24]
    # MCP bridge calls intentionally do not receive host-session-turn context.
    # Their tool name and host-issued call ID are sufficient to correlate a
    # failure without retaining arguments or exposing either identifier.
    mcp_fields = [params.get(key) for key in ("tool_name", "tool_call_id")]
    if all(isinstance(value, str) and value for value in mcp_fields):
        return sha256(
            json.dumps(["mcp", *mcp_fields], separators=(",", ":")).encode()
        ).hexdigest()[:24]
    return "unavailable"


class HookControlRuntime:
    """Run the enrolled boundary and its local hook-control socket together."""

    def __init__(
        self,
        config: ServiceAccountConfig,
        *,
        socket_path: str,
        participant: Optional[ConnectedParticipant] = None,
        rpc_timeout_seconds: float = 8.0,
        mcp_rpc_timeout_seconds: float = _MCP_RPC_TIMEOUT_SECONDS,
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
        self.mcp_rpc_timeout_seconds = max(0.1, float(mcp_rpc_timeout_seconds))
        self._posture_cache = NativeHookPostureCache(config)
        self._last_posture_document: Optional[str] = None
        self._observe_offline_permits = 0
        self._offline_permits: set[str] = set()
        self._event_health = HookEventHealth()
        self._server: Optional[asyncio.AbstractServer] = None
        self._socket_lock_fd: Optional[int] = None
        self._socket_identity: Optional[tuple[int, int]] = None
        self._reserving: set[str] = set()
        self._reservation_lock = asyncio.Lock()
        self._outbox = HookOutcomeOutbox(
            self.socket_path.with_name(f"{self.socket_path.name}.outcomes.json")
        )
        self._outbox_task: Optional[asyncio.Task[None]] = None
        self._outbox_wakeup = asyncio.Event()
        self._outbox_delivery_failures: dict[str, str] = {}
        self._stop_event = asyncio.Event()

    @property
    def started(self) -> bool:
        return self._server is not None

    @staticmethod
    def _action_key(*, host: str, session_id: str, turn_id: str, tool_call_id: str) -> str:
        """Build the host-provided correlation key for one governed tool call.

        Codex's documented post-tool event is correlated by its tool-use ID.
        A turn ID can be absent from that event even when it was supplied to
        preflight, so including it would turn a valid result observation into
        an unknown postflight.  The session-scoped tool-use ID is the complete
        correlation material for Codex.  Other hosts retain their full
        host-session-turn-tool binding.
        """

        fields = (
            (host, session_id, tool_call_id)
            if host == "codex"
            else (host, session_id, turn_id, tool_call_id)
        )
        return ":".join(fields)

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
        if self._server is not None:
            return
        self._ensure_socket_parent()
        self._acquire_socket_lock()
        try:
            self._remove_stale_socket()
            await self._outbox.load()
            await self.participant.start()
            self._server = await asyncio.start_unix_server(
                self._handle_connection,
                path=str(self.socket_path),
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
        except Exception:
            server, self._server = self._server, None
            if server is not None:
                server.close()
                await server.wait_closed()
            self._socket_identity = None
            await self.participant.stop()
            self._release_socket_lock()
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
        outbox_task, self._outbox_task = self._outbox_task, None
        if outbox_task is not None:
            outbox_task.cancel()
            await asyncio.gather(outbox_task, return_exceptions=True)
        try:
            if self._owns_socket_path():
                self.socket_path.unlink()
        finally:
            self._socket_identity = None
            try:
                await self.participant.stop()
            finally:
                self._release_socket_lock()

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
            timeout_seconds = (
                self.mcp_rpc_timeout_seconds
                if method == "mcp.invoke"
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
            is_policy_denial = isinstance(exc, (PolicyViolationError, ActionDenied))
            (_LOGGER.info if is_policy_denial else _LOGGER.warning)(
                "hook_control_request_%s method=%s code=%s correlation=%s elapsed_ms=%s",
                "denied" if is_policy_denial else "failed",
                method, code, _request_correlation(params), round((monotonic() - started_at) * 1000),
            )
            return {"id": request_id, "ok": False, "error": {"code": code}}
        finally:
            self._record_event_health(
                method=method,
                params=params,
                outcome=outcome,
                elapsed_ms=round((monotonic() - started_at) * 1_000),
            )

    def _record_event_health(
        self, *, method: str, params: Any, outcome: str, elapsed_ms: int
    ) -> None:
        """Publish only bounded event-health facts; never hook content."""

        self._event_health.record(
            method=method,
            params=params,
            outcome=outcome,
            elapsed_ms=elapsed_ms,
        )
        report = getattr(self.participant, "set_native_hook_event_receipts", None)
        if not callable(report):
            return
        try:
            report(event_receipts=self._event_health.snapshot())
        except ValueError:
            _LOGGER.warning("native_hook_event_health_rejected")

    async def _dispatch(self, method: str, raw_params: Any) -> Dict[str, Any]:
        if method == "health":
            if raw_params not in ({}, None):
                raise HookControlError("invalid_health_params")
            return {
                "protocol_version": HOOK_CONTROL_PROTOCOL,
                "capabilities": [_HOST_CAPABILITY],
                "undelivered_outcomes": self._outbox.undelivered_outcome_count,
                "native_hook_posture": self._posture_cache.diagnostics(),
                "observe_offline_permits": self._observe_offline_permits,
            }
        if method == "model.decision":
            return await self._model_decision(raw_params)
        if method == "action.preflight":
            return await self._preflight(raw_params)
        if method == "action.postflight":
            return await self._postflight(raw_params)
        if method == "mcp.invoke":
            return await self._invoke_mcp(raw_params)
        if method == "mcp.list":
            return await self._list_mcp_tools(raw_params)
        raise HookControlError("unsupported_method")

    async def _mcp_catalog(self) -> tuple[MCPProxyTool, ...]:
        payload = await self.governance.mcp_catalog_async()
        raw_tools = payload.get("tools")
        if not isinstance(raw_tools, list):
            raise HookControlError("mcp_catalog_invalid")
        tools: list[MCPProxyTool] = []
        names: set[str] = set()
        for raw_tool in raw_tools:
            if not isinstance(raw_tool, Mapping):
                raise HookControlError("mcp_catalog_invalid")
            tool = MCPProxyTool(
                name=raw_tool.get("name", ""),
                description=raw_tool.get("description", ""),
                input_schema=raw_tool.get("input_schema", {}),
                target_binding=raw_tool.get("target_binding", ""),
                target_tool_name=raw_tool.get("target_tool_name", ""),
            )
            if tool.name in names:
                raise HookControlError("mcp_catalog_invalid")
            names.add(tool.name)
            tools.append(tool)
        return tuple(tools)

    async def _list_mcp_tools(self, raw_params: Any) -> Dict[str, Any]:
        if raw_params not in ({}, None):
            raise HookControlError("invalid_mcp_list_params")
        return {
            "tools": [tool.as_mcp_tool() for tool in await self._mcp_catalog()],
        }

    async def _invoke_mcp(self, raw_params: Any) -> Dict[str, Any]:
        params = _object(
            raw_params,
            "params",
            allowed={"tool_name", "arguments", "tool_call_id"},
        )
        tool_name = _identifier(params.get("tool_name"), "tool_name")
        arguments = params.get("arguments")
        if not isinstance(arguments, Mapping):
            raise HookControlError("invalid_arguments")
        tool_call_id = _identifier(params.get("tool_call_id"), "tool_call_id")
        tool = next(
            (item for item in await self._mcp_catalog() if item.name == tool_name),
            None,
        )
        if tool is None:
            raise HookControlError("mcp_tool_not_assigned")
        try:
            response = await self.governance.mcp_communicate_async(
                target_binding=tool.target_binding,
                tool_name=tool.target_tool_name,
                arguments=dict(arguments),
                tool_call_id=tool_call_id,
            )
        except PolicyViolationError as exc:
            # The Gateway is authoritative for signed policy outcomes. Both
            # admission denials and withheld provider responses are valid MCP
            # tool results, not bridge or transport failures. Keep messages
            # stable and do not reflect detector or policy detail.
            details = exc.details if isinstance(exc.details, Mapping) else {}
            message = (
                "Atellagent withheld this tool response under the configured policy."
                if details.get("response_delivery_status") == "withheld"
                else "Atellagent blocked this tool call under the configured policy."
            )
            return {
                "content": [{"type": "text", "text": message}],
                "is_error": True,
            }
        result = _mcp_tool_result(response)
        return {
            "content": result["content"],
            "is_error": result.get("isError") is True,
        }

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
                "postflight_required", "adapter_version",
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
        adapter_version = _identifier(params.get("adapter_version"), "adapter_version")
        action_key = self._action_key(**fields)
        async with self._reservation_lock:
            if action_key in self._reserving:
                raise HookControlError("duplicate_tool_call")
            self._reserving.add(action_key)
        context = GovernanceCallContext(
            tool_name=tool_name,
            arguments=dict(arguments),
            task_type="host_tool",
            runtime_mode="hook",
            capabilities=[_HOST_CAPABILITY],
            tool_call_id=fields["tool_call_id"],
            action_key=action_key,
            request_payload={
                "host": fields["host"],
                "session_id": fields["session_id"],
                "turn_id": fields["turn_id"],
                "adapter_version": adapter_version,
            },
        )
        try:
            await self._refresh_native_hook_posture()
            receipt = await asyncio.wait_for(
                self.governance.preflight_async(context),
                timeout=min(self.rpc_timeout_seconds, _PREFLIGHT_TIMEOUT_SECONDS),
            )
            if not receipt.is_executable:
                raise HookControlError("policy_denied")
            if (
                receipt.action_key != action_key
                or not receipt.action_binding_fingerprint
            ):
                raise HookControlError("decision_binding_invalid")
            if postflight_required:
                try:
                    await self._outbox.reserve(
                        action_key, receipt.action_binding_fingerprint
                    )
                except ValueError as exc:
                    if "already exists" in str(exc):
                        raise HookControlError("duplicate_tool_call") from exc
                    raise HookControlError("outcome_delivery_unavailable") from exc
            if receipt.obligations:
                if not postflight_required:
                    try:
                        await self._outbox.reserve(
                            action_key, receipt.action_binding_fingerprint
                        )
                    except ValueError as exc:
                        raise HookControlError("outcome_delivery_unavailable") from exc
                await self._record_local_outcome(
                    action_key,
                    success=False,
                    outcome_observation="local_protocol_rejected",
                    error_type="UnsupportedObligation",
                )
                raise HookControlError("unsupported_obligation")
            try:
                await asyncio.wait_for(
                    self.governance.action_gate.enforce(
                        action=context.tool_name,
                        integration_type="agent",
                        correlation_id=receipt.action_key,
                        encoded_directive=receipt.control_directive,
                        facts=context.arguments,
                        workflow_context=receipt.workflow_context,
                        policy_decision_id=receipt.decision_id,
                    ),
                    timeout=min(
                        self.rpc_timeout_seconds,
                        _DIRECTIVE_VERIFICATION_TIMEOUT_SECONDS,
                    ),
                )
            except Exception as exc:
                if not postflight_required:
                    try:
                        await self._outbox.reserve(
                            action_key, receipt.action_binding_fingerprint
                        )
                    except ValueError as reserve_error:
                        raise HookControlError("outcome_delivery_unavailable") from reserve_error
                await self._record_local_outcome(
                    action_key,
                    success=False,
                    outcome_observation="local_directive_verification_failed",
                    error_type="ActionDenied",
                )
                if isinstance(exc, asyncio.TimeoutError):
                    raise HookControlError("control_directive_timeout") from exc
                raise HookControlError("control_directive_rejected") from exc
        except PolicyViolationError as exc:
            return {
                "allowed": False,
                "reason_code": str(exc.violation_type or "policy_denied"),
            }
        except httpx.TransportError:
            if self._posture_cache.posture and self._posture_cache.posture.permits_observe_outage(
                host=fields["host"], adapter_version=adapter_version
            ):
                self._offline_permits.add(action_key)
                self._observe_offline_permits += 1
                report_health = getattr(
                    self.participant,
                    "set_native_hook_coverage_health",
                    None,
                )
                if callable(report_health):
                    report_health(observe_offline_permits=self._observe_offline_permits)
                return {
                    "allowed": True,
                    "action_key": action_key,
                    "disposition": "observe_offline_permit",
                }
            raise
        finally:
            async with self._reservation_lock:
                self._reserving.discard(action_key)
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
                "result_payload", "error_message", "error_type", "outcome_observation",
            },
        )
        fields = self._turn_fields(params, include_tool=True)
        success_value = params.get("success")
        if success_value is not None and not isinstance(success_value, bool):
            raise HookControlError("invalid_success")
        outcome_observation = str(params.get("outcome_observation") or "").strip()
        if success_value is None and outcome_observation != "result_observed":
            raise HookControlError("invalid_outcome_observation")
        if not outcome_observation:
            outcome_observation = (
                "result_observed" if success_value is True else "execution_failed"
            )
        action_key = self._action_key(**fields)
        if action_key in self._offline_permits:
            self._offline_permits.discard(action_key)
            return {"recorded": False, "reason_code": "observe_offline_permit"}
        error_type = str(params.get("error_type") or "").strip() or None
        if error_type is not None:
            error_type = _identifier(error_type, "error_type")
        result_payload = params.get("result_payload") if success_value is not False else None
        result_byte_length: Optional[int] = None
        result_sha256: Optional[str] = None
        if result_payload is not None:
            try:
                serialized = json.dumps(
                    result_payload, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            except (TypeError, ValueError):
                serialized = None
            if serialized is not None:
                result_byte_length = len(serialized)
                result_sha256 = sha256(serialized).hexdigest()
        try:
            await self._record_local_outcome(
                action_key,
                success=success_value,
                outcome_observation=outcome_observation,
                error_type=error_type,
                result_byte_length=result_byte_length,
                result_sha256=result_sha256,
            )
        except HookOutcomeCorrelationUnavailable as exc:
            raise HookControlError("unknown_tool_call") from exc
        except (ValueError, OSError) as exc:
            raise HookControlError("outcome_delivery_unavailable") from exc
        return {"recorded": True, "action_key": action_key, "delivery_status": "queued"}

    async def _record_local_outcome(
        self,
        action_key: str,
        *,
        success: Optional[bool],
        outcome_observation: str,
        error_type: Optional[str] = None,
        result_byte_length: Optional[int] = None,
        result_sha256: Optional[str] = None,
    ) -> None:
        await self._outbox.record_outcome(
            action_key,
            {
                "outcome_observation": outcome_observation,
                "success": success,
                "result_byte_length": result_byte_length,
                "result_sha256": result_sha256,
                "error_type": error_type,
            },
        )
        self._outbox_wakeup.set()

    async def _run_outbox_delivery(self) -> None:
        """Retry only outcomes already received from the host hook."""

        while not self._stop_event.is_set():
            self._outbox_wakeup.clear()
            await self._outbox.prune()
            for action_key, entry in await self._outbox.received_entries():
                try:
                    outcome = entry.get("outcome")
                    fingerprint = entry.get("binding_fingerprint")
                    if not isinstance(outcome, dict) or not isinstance(fingerprint, str):
                        raise ValueError("hook outcome outbox is invalid")
                    await asyncio.wait_for(
                        self.governance.native_hook_outcome_async(
                            action_key=action_key,
                            action_binding_fingerprint=fingerprint,
                            outcome_observation=str(outcome["outcome_observation"]),
                            success=outcome.get("success"),
                            result_byte_length=outcome.get("result_byte_length"),
                            result_sha256=outcome.get("result_sha256"),
                            error_type=outcome.get("error_type"),
                        ),
                        timeout=min(self.rpc_timeout_seconds, 1.5),
                    )
                except Exception as exc:
                    failure_code = _safe_outcome_delivery_error_code(exc)
                    if isinstance(exc, PolicyViolationError):
                        # A native outcome is bound to an already-released
                        # action. A Gateway policy rejection therefore means
                        # this stored delivery can never become admissible
                        # (for example, its bounded correlation expired or
                        # belongs to a retired execution boundary). Retrying
                        # it forever cannot repair the canonical ledger.
                        _LOGGER.warning(
                            "native_hook_outcome_delivery_discarded code=%s correlation=%s",
                            failure_code,
                            sha256(action_key.encode()).hexdigest()[:24],
                        )
                        await self._outbox.acknowledge(action_key)
                        self._outbox_delivery_failures.pop(action_key, None)
                        continue
                    if self._outbox_delivery_failures.get(action_key) != failure_code:
                        _LOGGER.warning(
                            "native_hook_outcome_delivery_failed code=%s correlation=%s",
                            failure_code,
                            sha256(action_key.encode()).hexdigest()[:24],
                        )
                    self._outbox_delivery_failures[action_key] = failure_code
                    continue
                await self._outbox.acknowledge(action_key)
                self._outbox_delivery_failures.pop(action_key, None)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._outbox_wakeup.wait(), timeout=_OUTBOX_RETRY_INTERVAL_SECONDS
                )

    async def _refresh_native_hook_posture(self) -> None:
        document = getattr(self.participant, "native_hook_posture", None)
        if not document or document == self._last_posture_document:
            return
        await self._posture_cache.refresh(document)
        self._last_posture_document = document


__all__ = [
    "HOOK_CONTROL_PROTOCOL",
    "HookControlClient",
    "HookControlError",
    "HookControlRuntime",
]
