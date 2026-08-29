# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Connected participant lifecycle, registration, receive, and presence loops."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Set, Union

from cryptography import x509

from atellagent_client.governance import RuntimeActionGate
from atellagent_client.protocol.api import CLIENT_LIBRARY_VERSION
from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.gateway.session import GatewaySession

from .actions import ConnectedActionClient
from .capability import ConnectedCapabilityValidator, certificate_public_key_sha256
from .contracts import (
    ConnectedDelivery,
    ConnectedHandlerResult,
    ConnectedMessage,
    ConnectedProtocolError,
    parse_connected_message,
)
from .participant_delivery import ConnectedDeliveryMixin
from .participant_protocol import ConnectedHTTPError, strict_object as _strict_object
from .participant_rotation import ConnectedCertificateRotationMixin


logger = logging.getLogger(__name__)
ConnectedHandler = Callable[
    [ConnectedDelivery, ConnectedActionClient],
    Union[ConnectedHandlerResult, Awaitable[ConnectedHandlerResult]],
]


@dataclass(frozen=True)
class ConnectedOperationHandler:
    """Handler plus an explicit statement about external-effect retry safety."""

    handler: ConnectedHandler
    consequential: bool
    idempotency_mode: str

    def __post_init__(self) -> None:
        if self.idempotency_mode not in {"none", "target"}:
            raise ValueError("idempotency_mode must be 'none' or 'target'")
        if self.consequential and self.idempotency_mode != "target":
            raise ValueError(
                "consequential handlers must propagate delivery.idempotency_key "
                "to an idempotent target"
            )


class ConnectedParticipant(ConnectedDeliveryMixin, ConnectedCertificateRotationMixin):
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
        default_key = f"{socket.gethostname()}:{config.integration_id}:{config.packaging}"
        self.instance_key = str(instance_key or configured_key or default_key).strip()
        if not self.instance_key or len(self.instance_key) > 255:
            raise ValueError("instance_key must be between 1 and 255 characters")
        self.heartbeat_interval = max(5.0, float(heartbeat_interval))
        self.receive_wait_seconds = min(30, max(1, int(receive_wait_seconds)))
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
        self._instance_id: Optional[str] = None
        self._native_hook_posture: Optional[str] = None
        self._native_hook_observe_offline_permits = 0
        self._registration_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._receive_enabled = asyncio.Event()
        self._receive_enabled.set()
        self._rotation_lock = asyncio.Lock()
        self._started = False
        self._loop_tasks: Set[asyncio.Task[Any]] = set()
        self._delivery_tasks: Set[asyncio.Task[Any]] = set()

    @property
    def instance_id(self) -> Optional[str]:
        return self._instance_id

    @property
    def native_hook_posture(self) -> Optional[str]:
        return self._native_hook_posture

    def set_native_hook_coverage_health(self, *, observe_offline_permits: int) -> None:
        """Publish an aggregate local observe-outage count on the next heartbeat."""

        if isinstance(observe_offline_permits, bool):
            raise ValueError("observe_offline_permits must be an integer")
        normalized = int(observe_offline_permits)
        if normalized < 0 or normalized > 1_000_000:
            raise ValueError("observe_offline_permits is outside the accepted range")
        self._native_hook_observe_offline_permits = normalized

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

    def _add_handler(
        self, operation: str, registration: ConnectedOperationHandler
    ) -> None:
        normalized = str(operation or "").strip()
        if not normalized or len(normalized) > 64:
            raise ValueError("operation must be between 1 and 64 characters")
        if "*" in normalized and not normalized.endswith(".*"):
            raise ValueError("operation wildcard is supported only as a trailing .* suffix")
        if normalized in self._handlers:
            raise ValueError(f"handler already registered for {normalized}")
        self._handlers[normalized] = registration

    def register_handler(
        self,
        operation: str,
        handler: ConnectedHandler,
        *,
        consequential: bool,
        idempotency_mode: str = "none",
    ) -> None:
        self._add_handler(
            operation,
            ConnectedOperationHandler(
                handler=handler,
                consequential=bool(consequential),
                idempotency_mode=idempotency_mode,
            ),
        )

    def _handler_for_operation(
        self, operation: str
    ) -> Optional[ConnectedOperationHandler]:
        exact = self._handlers.get(operation)
        if exact is not None:
            return exact
        matches = [
            (pattern[:-1], registration)
            for pattern, registration in self._handlers.items()
            if pattern.endswith(".*") and operation.startswith(pattern[:-1])
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: len(item[0]), reverse=True)
        return matches[0][1]

    def _url(self, path: str, *, message: Optional[ConnectedMessage] = None) -> str:
        rendered = str(path).format(
            instance_id=self._instance_id or "",
            message_id=message.message_id if message else "",
            lease_id=message.lease.lease_id if message else "",
        )
        if not rendered.startswith("/") or "://" in rendered:
            raise ConnectedProtocolError("connected runtime path is invalid")
        return f"{self.session.base_url}{rendered}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        message: Optional[ConnectedMessage] = None,
        json: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        response = await self.session.request_authenticated(
            method,
            self._url(path, message=message),
            json=dict(json) if json is not None else None,
        )
        if response.http_version != "HTTP/2":
            raise ConnectedProtocolError("connected runtime request did not use HTTP/2")
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text
            raise ConnectedHTTPError(response.status_code, str(detail or "request failed"))
        return response

    async def _rotation_request(
        self,
        method: str,
        path_template: str,
        *,
        operation_id: Optional[str] = None,
        json: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        rendered = str(path_template).format(
            instance_id=self._instance_id or "",
            operation_id=operation_id or "",
        )
        if not rendered.startswith("/") or "://" in rendered:
            raise ConnectedProtocolError("certificate rotation path is invalid")
        response = await self.session.request_authenticated(
            method,
            f"{self.session.base_url}{rendered}",
            json=dict(json) if json is not None else None,
        )
        if response.http_version != "HTTP/2":
            raise ConnectedProtocolError(
                "certificate rotation request did not use HTTP/2"
            )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text
            raise ConnectedHTTPError(
                response.status_code,
                str(detail or "certificate rotation request failed"),
            )
        return response

    async def _register(self) -> None:
        async with self._registration_lock:
            if self._instance_id is not None:
                return
            response = await self._request(
                "POST",
                self.config.registration_path,
                json={
                    "instance_key": self.instance_key,
                    "protocol_version": self.config.protocol_version,
                    "client_version": CLIENT_LIBRARY_VERSION,
                    "capabilities": self.config.capabilities,
                },
            )
            payload = _strict_object(
                response.json(),
                {
                    "instance_id", "protocol_version", "presence_status", "registered_at",
                    "receive_path", "heartbeat_path", "drain_path",
                },
                "registration response",
            )
            if payload.get("protocol_version") != "v1":
                raise ConnectedProtocolError("gateway selected an unsupported protocol")
            self._instance_id = str(payload.get("instance_id") or "").strip()
            if not self._instance_id:
                raise ConnectedProtocolError("registration response has no instance_id")
            try:
                if self._mcp_manifest is not None:
                    await self._publish_mcp_descriptor()
            except Exception:
                self._instance_id = None
                raise

    async def _publish_mcp_descriptor(self) -> None:
        if not self.config.mcp_descriptor_path_template or self._mcp_manifest is None:
            return
        response = await self._request(
            "PUT", self.config.mcp_descriptor_path_template,
            json={
                "protocol_version": self.config.protocol_version,
                "manifest": self._mcp_manifest,
                "expected_previous_manifest_hash": None,
            },
        )
        _strict_object(
            response.json(),
            {"integration_id", "manifest_hash", "descriptor_revision", "tool_count", "published_at"},
            "MCP descriptor response",
        )

    async def _ensure_registered(self) -> None:
        if self._instance_id is None and not self._stop_event.is_set():
            await self._register()

    async def _receive_once(self) -> None:
        await self._ensure_registered()
        response = await self._request(
            "POST", self.config.receive_path_template,
            json={"wait_seconds": self.receive_wait_seconds},
        )
        if response.status_code == 204:
            return
        outer = _strict_object(response.json(), {"message"}, "receive response")
        if outer.get("message") is None:
            return
        message = parse_connected_message(outer["message"])
        if message.kind == "control":
            if message.operation != "certificate.rotate":
                await self._acknowledge(message, "rejected", "unsupported_control_operation")
                return
            try:
                await self._validator.validate(message)
            except ConnectedProtocolError:
                await self._acknowledge(message, "rejected", "invalid_capability")
                raise
            self._receive_enabled.clear()
            try:
                await self._acknowledge(message, "accepted")
            except Exception:
                self._receive_enabled.set()
                raise
            self._track_delivery(asyncio.create_task(self._process_certificate_rotation(message)))
            return
        registration = self._handler_for_operation(message.operation)
        if registration is None:
            await self._acknowledge(message, "rejected", "unsupported_operation")
            return
        try:
            await self._validator.validate(message)
        except ConnectedProtocolError:
            await self._acknowledge(message, "rejected", "invalid_capability")
            raise
        await self._acknowledge(message, "accepted")
        self._track_delivery(asyncio.create_task(self._process(message, registration.handler)))

    async def _receive_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._receive_enabled.wait()
                await self._receive_once()
            except asyncio.CancelledError:
                raise
            except ConnectedHTTPError as exc:
                if exc.status_code in {403, 404}:
                    self._instance_id = None
                logger.warning("connected receive failed: %s", exc)
                await asyncio.sleep(random.uniform(0.5, 2.0))
            except Exception:
                logger.exception("connected receive loop failed")
                await asyncio.sleep(random.uniform(0.5, 2.0))

    async def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.heartbeat_interval)
                continue
            except asyncio.TimeoutError:
                pass
            try:
                await self._receive_enabled.wait()
                await self._ensure_registered()
                certificate = x509.load_pem_x509_certificate(Path(str(self.config.cert_path)).read_bytes())
                heartbeat_payload = {
                    "protocol_version": self.config.protocol_version,
                    "capabilities": self.config.capabilities,
                    "certificate_public_key_sha256": certificate_public_key_sha256(str(self.config.cert_path)),
                    "certificate_expires_at": certificate.not_valid_after_utc.isoformat(),
                }
                # Native-hook coverage is optional telemetry, not part of a
                # connected runtime's base liveness protocol. The gateway
                # establishes whether this identity is a reviewed native host
                # by returning a signed posture on an ordinary heartbeat.
                if self._native_hook_posture:
                    heartbeat_payload["native_hook_coverage_health"] = {
                        "observe_offline_permits": self._native_hook_observe_offline_permits,
                    }
                response = await self._request(
                    "POST", self.config.heartbeat_path_template,
                    json=heartbeat_payload,
                )
                payload = _strict_object(
                    response.json(),
                    {"instance_id", "presence_status", "heartbeat_at", "native_hook_posture"},
                    "heartbeat response",
                )
                if str(payload.get("instance_id")) != self._instance_id:
                    raise ConnectedProtocolError("heartbeat response binding mismatch")
                posture = payload.get("native_hook_posture")
                if posture is not None and not isinstance(posture, str):
                    raise ConnectedProtocolError("native hook posture response is invalid")
                if isinstance(posture, str) and posture.strip():
                    self._native_hook_posture = posture.strip()
                else:
                    self._native_hook_posture = None
            except ConnectedHTTPError as exc:
                if exc.status_code in {403, 404}:
                    self._instance_id = None
                    self._native_hook_posture = None
                logger.warning("connected heartbeat failed: %s", exc)
            except Exception:
                logger.exception("connected heartbeat loop failed")

    async def start(self) -> None:
        if self._started:
            return
        self._stop_event.clear()
        await self._register()
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
            raise RuntimeError("credential reload requires a participant-owned GatewaySession")
        was_started = self._started
        await self.stop(grace_seconds=grace_seconds)
        self.session = GatewaySession.from_service_account_config(self.config)
        self._validator = ConnectedCapabilityValidator(self.config, self.session)
        if was_started:
            await self.start()

    async def stop(self, *, grace_seconds: float = 30.0) -> None:
        if not self._started:
            if self._owns_session:
                await self.session.close_async()
            return
        self._stop_event.set()
        if self._instance_id:
            try:
                response = await self._request("POST", self.config.drain_path_template, json={"mode": "graceful"})
                payload = _strict_object(
                    response.json(), {"instance_id", "presence_status", "drain_requested_at"}, "drain response"
                )
                if str(payload.get("instance_id")) != self._instance_id or payload.get("presence_status") != "draining":
                    raise ConnectedProtocolError("drain response binding mismatch")
            except Exception:
                logger.exception("connected participant drain failed")
        for task in self._loop_tasks:
            task.cancel()
        await asyncio.gather(*self._loop_tasks, return_exceptions=True)
        if self._delivery_tasks:
            _done, pending = await asyncio.wait(set(self._delivery_tasks), timeout=max(0.0, float(grace_seconds)))
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        if self._instance_id:
            try:
                response = await self._request("DELETE", self.config.deregistration_path_template)
                if response.status_code != 204:
                    raise ConnectedProtocolError("deregistration response status is invalid")
            except Exception:
                logger.exception("connected participant deregistration failed")
        self._instance_id = None
        self._started = False
        if self._owns_session:
            await self.session.close_async()

    async def __aenter__(self) -> "ConnectedParticipant":
        await self.start()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        await self.stop()


__all__ = [
    "ConnectedHandler", "ConnectedHTTPError", "ConnectedOperationHandler", "ConnectedParticipant",
]
