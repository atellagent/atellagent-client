# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Connected participant construction and lifecycle orchestration."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import logging
import os
import socket
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Set


from atellagent_client.governance import RuntimeActionGate
from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.gateway.session import GatewaySession

from .capability import ConnectedCapabilityValidator
from .contracts import (
    ConnectedProtocolError,
)
from .participant_delivery import ConnectedDeliveryMixin
from .participant_protocol import ConnectedHTTPError, strict_object as _strict_object
from .participant_rotation import ConnectedCertificateRotationMixin


from .participant_handlers import (
    ConnectedHandler,
    ConnectedOperationHandler,
    ConnectedHandlerRegistryMixin,
)
from .participant_transport import ConnectedTransportMixin
from .participant_registration import ConnectedRegistrationMixin
from .participant_receiver import ConnectedReceiverMixin
from .participant_health import ConnectedHealthMixin

logger = logging.getLogger(__name__)


class ConnectedParticipant(
    ConnectedHandlerRegistryMixin,
    ConnectedTransportMixin,
    ConnectedRegistrationMixin,
    ConnectedReceiverMixin,
    ConnectedHealthMixin,
    ConnectedDeliveryMixin,
    ConnectedCertificateRotationMixin,
):
    """Own participant registration, receiver presence, and inbound dispatch."""

    def __init__(
        self,
        config: ServiceAccountConfig,
        *,
        handlers: Optional[Mapping[str, ConnectedOperationHandler]] = None,
        instance_key: Optional[str] = None,
        heartbeat_interval: float = 20.0,
        receive_wait_seconds: int = 25,
        max_concurrency: int = 8,
        mcp_manifest: Optional[Mapping[str, Any]] = None,
        session: Optional[GatewaySession] = None,
    ) -> None:
        self.config = config
        self.session = session or GatewaySession.from_service_account_config(config)
        self._owns_session = session is None
        self._validator = ConnectedCapabilityValidator(config, self.session)
        self._local_action_gate = (
            RuntimeActionGate.from_local_manifest(
                str(config.local_guardrail_manifest_path),
                expected_mode=config.local_guardrail_mode,
            )
            if config.control_source == "local_manifest"
            else None
        )
        self._handlers: Dict[str, ConnectedOperationHandler] = {}
        for operation, registration in (handlers or {}).items():
            if not isinstance(registration, ConnectedOperationHandler):
                raise TypeError(
                    "handlers values must be ConnectedOperationHandler instances"
                )
            self._add_handler(operation, registration)
        self._mcp_manifest = dict(mcp_manifest) if mcp_manifest is not None else None
        if config.integration_type == "mcp":
            if not config.mcp_descriptor_path_template:
                raise ValueError("connected MCP configuration has no descriptor path")
            if self._mcp_manifest is None:
                raise ValueError("connected MCP participants require an MCP manifest")
        configured_key = str(os.getenv("ATELLAGENT_INSTANCE_KEY") or "").strip()
        default_key = (
            f"{socket.gethostname()}:{config.integration_id}:{config.packaging}"
        )
        self.instance_key = str(instance_key or configured_key or default_key).strip()
        self._base_instance_key = self.instance_key
        if not self.instance_key or len(self.instance_key) > 255:
            raise ValueError("instance_key must be between 1 and 255 characters")
        self.heartbeat_interval = max(5.0, float(heartbeat_interval))
        self.receive_wait_seconds = min(30, max(1, int(receive_wait_seconds)))
        self._max_concurrency = max(1, int(max_concurrency))
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._receive_lock = asyncio.Lock()
        self._instance_id: Optional[str] = None
        self._native_hook_posture: Optional[str] = None
        self._native_hook_observe_offline_permits = 0
        self._native_hook_event_receipts: list[dict[str, object]] = []
        self._registration_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._receive_enabled = asyncio.Event()
        self._receive_enabled.set()
        self._rotation_lock = asyncio.Lock()
        self._started = False
        self._lifecycle_lock = asyncio.Lock()
        self._session_closed = False
        self._loop_tasks: Set[asyncio.Task[Any]] = set()
        self._delivery_tasks: Set[asyncio.Task[Any]] = set()
        self._device_proof_provider: Optional[
            Callable[[str, str, Any], Mapping[str, str]]
        ] = None
        self._device_registration_refresher: Optional[Callable[[], Awaitable[None]]] = (
            None
        )

    def set_device_proof_provider(
        self, provider: Callable[[str, str, Any], Mapping[str, str]]
    ) -> None:
        """Bind this participant's admission to its registered local installation."""
        if self._started:
            raise RuntimeError(
                "device proof must be configured before participant start"
            )
        self._device_proof_provider = provider
        self.session.device_proof_provider = provider

    def set_device_registration_refresher(
        self, refresher: Callable[[], Awaitable[None]]
    ) -> None:
        if self._started:
            raise RuntimeError(
                "device refresh must be configured before participant start"
            )
        self._device_registration_refresher = refresher

    def set_device_instance_key(self, device_id: str) -> None:
        """A changed local user/device must not inherit an old receiver instance."""
        scoped_key = (
            f"{sha256(self._base_instance_key.encode()).hexdigest()[:32]}:{device_id}"
        )
        if self.instance_key != scoped_key:
            self.instance_key = scoped_key
            self._instance_id = None
            self._native_hook_posture = None

    @property
    def instance_id(self) -> Optional[str]:
        return self._instance_id

    async def enforce_local_action(
        self,
        *,
        action: str,
        correlation_id: str,
        facts: Mapping[str, Any],
    ) -> None:
        """Apply the explicitly selected free local control source."""
        if self.config.control_source != "local_manifest":
            return
        if self._local_action_gate is None:
            raise ConnectedProtocolError("local action control is unavailable")
        await self._local_action_gate.enforce(
            action=action,
            integration_type="mcp",
            correlation_id=correlation_id,
            facts=facts,
        )

    async def start(self) -> None:
        async with self._lifecycle_lock:
            await self._start_locked()

    async def _start_locked(self) -> None:
        if self._started:
            return
        self._stop_event.clear()
        self._session_closed = False
        try:
            await self._register()
        except BaseException as exc:
            try:
                await self._stop_locked(grace_seconds=0)
            except Exception as cleanup_error:
                exc.add_note(f"Participant startup cleanup also failed: {cleanup_error!r}")
            raise
        self._started = True
        self._loop_tasks = {
            asyncio.create_task(self._receive_loop()),
            asyncio.create_task(self._heartbeat_loop()),
        }

    async def run_forever(self) -> None:
        await self.start()
        await self._stop_event.wait()

    async def reload_client_certificate(self, *, grace_seconds: float = 30.0) -> None:
        """Drain, rebuild TLS/auth state from replaced files, and reconnect."""
        if not self._owns_session:
            raise RuntimeError(
                "credential reload requires a participant-owned GatewaySession"
            )
        async with self._lifecycle_lock:
            was_started = self._started
            await self._stop_locked(grace_seconds=grace_seconds)
            self.session = GatewaySession.from_service_account_config(
                self.config, device_proof_provider=self._device_proof_provider
            )
            self._session_closed = False
            self._validator = ConnectedCapabilityValidator(self.config, self.session)
            if was_started:
                await self._start_locked()

    async def stop(self, *, grace_seconds: float = 30.0) -> None:
        self._stop_event.set()
        async with self._lifecycle_lock:
            await self._stop_locked(grace_seconds=grace_seconds)

    async def _stop_locked(self, *, grace_seconds: float) -> None:
        self._stop_event.set()
        # The lifecycle lock remains held until this exact cleanup task finishes.
        # Caller cancellation cannot be forwarded into loop/delivery finalizers.
        cleanup = asyncio.create_task(
            self._finish_stop(grace_seconds=grace_seconds),
            name="connected-participant-cleanup",
        )
        joined = asyncio.gather(cleanup, return_exceptions=True)
        cancelled = None
        while True:
            try:
                outcome = (await asyncio.shield(joined))[0]
                break
            except asyncio.CancelledError as exc:
                cancelled = exc
        if cancelled is not None:
            if isinstance(outcome, Exception):
                cancelled.add_note(f"Participant cleanup also failed: {outcome!r}")
            raise cancelled
        if isinstance(outcome, BaseException):
            raise outcome

    async def _finish_stop(self, *, grace_seconds: float) -> None:
        if self._instance_id:
            try:
                # Remote presence is advisory to cleanup; an unreachable server
                # must not consume the full HTTP timeout before local tasks stop.
                async with asyncio.timeout(2.0):
                    response = await self._request(
                        "POST", self.config.drain_path_template, json={"mode": "graceful"}
                    )
                payload = _strict_object(
                    response.json(),
                    {"instance_id", "presence_status", "drain_requested_at"},
                    "drain response",
                )
                if (
                    str(payload.get("instance_id")) != self._instance_id
                    or payload.get("presence_status") != "draining"
                ):
                    raise ConnectedProtocolError("drain response binding mismatch")
            except Exception:
                logger.exception("connected participant drain failed")
        for task in self._loop_tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*self._loop_tasks, return_exceptions=True)
        self._loop_tasks.clear()
        if self._delivery_tasks:
            _done, pending = await asyncio.wait(
                set(self._delivery_tasks), timeout=max(0.0, float(grace_seconds))
            )
            for task in pending:
                if not task.done() and not task.cancelling():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        if self._instance_id:
            try:
                async with asyncio.timeout(2.0):
                    response = await self._request(
                        "DELETE", self.config.deregistration_path_template
                    )
                if response.status_code != 204:
                    raise ConnectedProtocolError(
                        "deregistration response status is invalid"
                    )
            except Exception:
                logger.exception("connected participant deregistration failed")
        self._instance_id = None
        self._started = False
        if self._owns_session and not self._session_closed:
            await self.session.close_async()
            self._session_closed = True

    async def __aenter__(self) -> "ConnectedParticipant":
        await self.start()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        await self.stop()


__all__ = [
    "ConnectedHandler",
    "ConnectedHTTPError",
    "ConnectedOperationHandler",
    "ConnectedParticipant",
]
