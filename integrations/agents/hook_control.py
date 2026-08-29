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

import httpx

from atellagent_client.connected import ConnectedParticipant
from atellagent_client.governance import ActionDenied
from atellagent_client.protocol.agent_contracts import (
    GovernanceCallContext,
    GovernanceReceipt,
    ModelDecisionRequest,
)
from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError
from atellagent_client.proxy.contracts import MCPProxyTool
from atellagent_client.sdk.client_modules.mcp_tools import _mcp_tool_result

from .control import ExternalAgentGovernance
from .hook_control_protocol import HookControlClient, HookControlError
from .native_hook_posture import NativeHookPostureCache


HOOK_CONTROL_PROTOCOL = "atellagent.hook-control.v1"
_MAX_REQUEST_BYTES = 64 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_HOST_CAPABILITY = "agent.control"


@dataclass(frozen=True)
class _PendingAction:
    context: GovernanceCallContext
    receipt: GovernanceReceipt
    success: Optional[bool] = None
    result_payload: Any = None
    error_message: Optional[str] = None
    error_type: Optional[str] = None
    evidence: Optional[Dict[str, Any]] = None
    postflight_received: bool = False


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


class HookControlRuntime:
    """Run the enrolled boundary and its local hook-control socket together."""

    def __init__(
        self,
        config: ServiceAccountConfig,
        *,
        socket_path: str,
        participant: Optional[ConnectedParticipant] = None,
        rpc_timeout_seconds: float = 8.0,
        mcp_rpc_timeout_seconds: float = 305.0,
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
        self.mcp_rpc_timeout_seconds = max(0.1, float(mcp_rpc_timeout_seconds))
        self.postflight_attempts = max(1, int(postflight_attempts))
        self._posture_cache = NativeHookPostureCache(config)
        self._last_posture_document: Optional[str] = None
        self._observe_offline_permits = 0
        self._offline_permits: set[str] = set()
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
            timeout_seconds = (
                self.mcp_rpc_timeout_seconds
                if method == "mcp.invoke"
                else self.rpc_timeout_seconds
            )
            result = await asyncio.wait_for(
                self._dispatch(method, params),
                timeout=timeout_seconds,
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
                unresolved = len(self._pending)
            return {
                "protocol_version": HOOK_CONTROL_PROTOCOL,
                "capabilities": [_HOST_CAPABILITY],
                "unresolved_postflights": unresolved,
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
        response = await self.governance.mcp_communicate_async(
            target_binding=tool.target_binding,
            tool_name=tool.target_tool_name,
            arguments=dict(arguments),
            tool_call_id=tool_call_id,
        )
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
                "adapter_version": adapter_version,
            },
        )
        try:
            await self._refresh_native_hook_posture()
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
                    policy_decision_id=receipt.decision_id,
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
        except httpx.TransportError:
            async with self._pending_lock:
                self._reserving.discard(pending_key)
                if self._posture_cache.posture and self._posture_cache.posture.permits_observe_outage(
                    host=fields["host"], adapter_version=adapter_version
                ):
                    self._offline_permits.add(pending_key)
                    self._observe_offline_permits += 1
                    report_health = getattr(
                        self.participant,
                        "set_native_hook_coverage_health",
                        None,
                    )
                    if callable(report_health):
                        report_health(
                            observe_offline_permits=self._observe_offline_permits
                        )
                    return {
                        "allowed": True,
                        "action_key": pending_key,
                        "disposition": "observe_offline_permit",
                    }
            raise
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
        pending_key = self._pending_key(**fields)
        async with self._pending_lock:
            pending = self._pending.get(pending_key)
            if pending is None:
                if pending_key in self._offline_permits:
                    self._offline_permits.discard(pending_key)
                    return {"recorded": False, "reason_code": "observe_offline_permit"}
                raise HookControlError("unknown_tool_call")
            success = success_value
            if pending.postflight_received and pending.success != success:
                raise HookControlError("postflight_conflict")
            if not pending.postflight_received:
                pending = _PendingAction(
                    context=pending.context,
                    receipt=pending.receipt,
                    success=success,
                    result_payload=params.get("result_payload") if success is not False else None,
                    error_message=str(params.get("error_message") or "").strip() or None,
                    error_type=str(params.get("error_type") or "").strip() or None,
                    evidence=(
                        {"outcome_observation": outcome_observation}
                        if outcome_observation
                        else None
                    ),
                    postflight_received=True,
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
                    success=pending.success,
                    error_message=pending.error_message,
                    error_type=pending.error_type,
                    evidence=pending.evidence,
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
