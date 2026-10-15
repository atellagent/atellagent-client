# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Owner-private local control service for supported external-agent hooks.

The service is deliberately a narrow transport and policy-enforcement point.
It authenticates to Atellagent through its enrolled connected participant; hook
processes only receive safe allow/deny results and never receive credentials.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from hashlib import sha256
import logging
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


from atellagent_client.connected import ConnectedParticipant
from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.errors import PolicyViolationError
from atellagent_client.proxy.contracts import MCPVisibleTool
from atellagent_client.onboarding.native_runtime_binding import NativeRuntimeBinding
from atellagent_client.onboarding.native_runtime_identity import require_native_runtime_identity

from .control import ExternalAgentGovernance
from .hook_control_protocol import HookControlClient, HookControlError
from .hook_control_helpers import (
    _controller_diagnostic_code,  # noqa: F401 - compatibility diagnostic import
    _identifier,
    _request_correlation,  # noqa: F401 - compatibility import for hook diagnostics
    _safe_error_code,  # noqa: F401 - compatibility import for hook diagnostics
    _safe_outcome_delivery_error_code,
)
from .hook_health import HookEventHealth
from .hook_outbox import HookOutcomeOutbox
from .hook_verification import record_accepted
from .native_hook_posture import NativeHookPostureCache
from .hook_control_actions import HookControlActionMixin, _HOST_CAPABILITY
from .hook_control_transport import HookControlTransportMixin
from .windows_named_pipe import (
    WindowsNamedPipeServer,
    validate_windows_named_pipe,
    windows_local_state_path,
)


HOOK_CONTROL_PROTOCOL = "atellagent.hook-control.v1"
_MCP_RPC_TIMEOUT_SECONDS = 30.0
_OUTCOME_DELIVERY_TIMEOUT_SECONDS = 5.0
_OUTBOX_RETRY_INTERVAL_SECONDS = 5.0
_OUTBOX_MAINTENANCE_INTERVAL_SECONDS = 5.0
_LOGGER = logging.getLogger(__name__)

class HookControlRuntime(HookControlTransportMixin, HookControlActionMixin):
    """Run the enrolled boundary and its owner-private local control endpoint."""

    def __init__(
        self,
        config: ServiceAccountConfig,
        *,
        socket_path: str,
        native_binding: NativeRuntimeBinding,
        participant: Optional[ConnectedParticipant] = None,
        rpc_timeout_seconds: float = 18.0,
        mcp_rpc_timeout_seconds: float = _MCP_RPC_TIMEOUT_SECONDS,
    ) -> None:
        if config.integration_type != "agent":
            raise ValueError("hook control requires a connected agent integration")
        if config.identity_mode != "boundary_identity_only":
            raise ValueError("hook control requires boundary_identity_only identity mode")
        if set(config.capabilities) != {_HOST_CAPABILITY}:
            raise ValueError("hook control requires only the provisioned agent.control capability")
        require_native_runtime_identity(config, native_binding)
        self._native_binding = native_binding
        self._uses_windows_named_pipe = os.name == "nt"
        if self._uses_windows_named_pipe:
            endpoint = validate_windows_named_pipe(socket_path)
            self.socket_path: Path | str = endpoint
            outbox_path = windows_local_state_path(endpoint)
        else:
            candidate = Path(socket_path).expanduser()
            if not candidate.is_absolute() or not candidate.name:
                raise ValueError("hook control socket_path must be an absolute path")
            self.socket_path = candidate
            outbox_path = self.socket_path.with_name(f"{self.socket_path.name}.outcomes.json")
        self.config = config
        self.participant = participant or ConnectedParticipant(config)
        if self.participant.config is not config:
            raise ValueError("hook control participant must use the enrolled configuration")
        self.governance = ExternalAgentGovernance(
            config,
            session_provider=lambda: self.participant.session,
            device_proof_provider=native_binding.request_headers,
        )
        self.rpc_timeout_seconds = max(0.1, float(rpc_timeout_seconds))
        self.mcp_rpc_timeout_seconds = max(0.1, float(mcp_rpc_timeout_seconds))
        self._posture_cache = NativeHookPostureCache(config)
        self._last_posture_document: Optional[str] = None
        self._event_health = HookEventHealth()
        self._server: Optional[asyncio.AbstractServer] = None
        self._windows_server: Optional[WindowsNamedPipeServer] = None
        self._socket_lock_fd: Optional[int] = None
        self._socket_identity: Optional[tuple[int, int]] = None
        self._reserving: set[str] = set()
        self._reservation_lock = asyncio.Lock()
        self._outbox = HookOutcomeOutbox(
            outbox_path
        )
        self._outbox_task: Optional[asyncio.Task[None]] = None
        self._outbox_maintenance_task: Optional[asyncio.Task[None]] = None
        self._outbox_wakeup = asyncio.Event()
        self._outbox_delivery_failures: dict[str, str] = {}
        self._last_controller_diagnostic: Optional[str] = None
        self._stop_event = asyncio.Event()

    def _bind_native_participant(self):
        self.participant.set_device_instance_key(str(self._native_binding.device.device_id))
        self.participant.set_device_proof_provider(self._native_binding.request_headers)

    @property
    def started(self) -> bool:
        return self._server is not None or self._windows_server is not None

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
        if callable(report):
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
                "outcome_backlog": self._outbox.diagnostics(),
                "native_hook_posture": self._posture_cache.diagnostics(),
                "native_delegation": {
                    "authorization_id": str(self._native_binding.authorization_id),
                    "authorization_epoch": self._native_binding.authorization_epoch,
                    "authority_source": "gateway_admission",
                },
                "native_binding": {
                    "request_id": str(self._native_binding.request_id),
                    "device_id": str(self._native_binding.device.device_id),
                    "tenant_id": str(self._native_binding.device.tenant_id),
                    "owner_user_id": str(self._native_binding.device.owner_user_id),
                    "native_host": self.config.native_host,
                },
                "last_controller_diagnostic": self._last_controller_diagnostic,
            }
        if method == "model.decision":
            return await self._model_decision(raw_params)
        if method == "action.preflight":
            return await self._bounded_preflight(raw_params)
        if method == "action.postflight":
            return await self._postflight(raw_params)
        if method == "mcp.invoke":
            return await self._invoke_mcp(raw_params)
        if method == "mcp.list":
            return await self._list_mcp_tools(raw_params)
        raise HookControlError("unsupported_method")

    async def _mcp_catalog(self) -> tuple[MCPVisibleTool, ...]:
        from .hook_control_mcp import mcp_catalog

        return await mcp_catalog(self)

    async def _list_mcp_tools(self, raw_params: Any) -> Dict[str, Any]:
        from .hook_control_mcp import list_mcp_tools

        return await list_mcp_tools(self, raw_params)

    async def _invoke_mcp(self, raw_params: Any) -> Dict[str, Any]:
        from .hook_control_mcp import invoke_mcp

        return await invoke_mcp(self, raw_params)

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
        from .hook_control_model import model_decision

        return await model_decision(self, raw_params)


    async def _run_outbox_maintenance(self) -> None:
        """Expire missing callbacks independently of network delivery progress."""

        while not self._stop_event.is_set():
            try:
                await self._outbox.prune()
            except Exception:
                _LOGGER.warning("native_hook_outcome_cleanup_failed code=outcome_storage_unavailable")
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=_OUTBOX_MAINTENANCE_INTERVAL_SECONDS
                )

    async def _run_outbox_delivery(self) -> None:
        """Retry only outcomes already received from the host hook."""

        while not self._stop_event.is_set():
            self._outbox_wakeup.clear()
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
                        # This is an asynchronous, durable post-execution
                        # delivery—not a host-hook decision. It must not
                        # inherit the tight preflight budget and manufacture
                        # retries while Gateway is completing its ledger and
                        # audit writes.
                        timeout=min(
                            self.rpc_timeout_seconds,
                            _OUTCOME_DELIVERY_TIMEOUT_SECONDS,
                        ),
                    )
                except Exception as exc:
                    failure_code = _safe_outcome_delivery_error_code(exc)
                    if failure_code == "outcome_delivery_receipt_expired":
                        self._last_controller_diagnostic = "outcome_receipt_expired"
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
                try:
                    await asyncio.to_thread(record_accepted, self.socket_path, self._native_binding, action_key, entry)
                except (OSError, ValueError, TypeError, KeyError):
                    # Setup evidence must never block already-accepted delivery.
                    _LOGGER.warning("native_hook_setup_evidence_unavailable")
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
