# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Shared outbound connected participant used by both SDK and bridge packaging."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import random
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Set, Union

from cryptography import x509

from atellagent_client.protocol.api import CLIENT_LIBRARY_VERSION
from atellagent_client.governance import RuntimeActionGate
from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.enrollment import (
    commit_staged_certificate_rotation,
    discard_staged_certificate_rotation,
    prepare_certificate_rotation,
    stage_certificate_rotation,
)
from atellagent_client.sdk.gateway.session import GatewaySession

from .actions import ConnectedActionClient, _DeliveryActionContext
from .capability import (
    ConnectedCapabilityValidator,
    certificate_public_key_sha256,
)
from .contracts import (
    ConnectedDelivery,
    ConnectedHandlerResult,
    ConnectedMessage,
    ConnectedProtocolError,
    parse_connected_message,
)


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


class ConnectedHTTPError(ConnectedProtocolError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"connected gateway request failed ({status_code}): {detail}")
        self.status_code = int(status_code)
        self.detail = detail


class _CertificateRotationActivatedError(ConnectedProtocolError):
    """The cluster cutover committed, so the old delivery cannot be failed."""


def _strict_object(value: Any, expected: Set[str], name: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConnectedProtocolError(f"{name} must be an object")
    payload = dict(value)
    extra = set(payload) - expected
    if extra:
        raise ConnectedProtocolError(
            f"{name} contains unsupported fields: {', '.join(sorted(extra))}"
        )
    return payload


class ConnectedParticipant:
    """Own registration, receive, acknowledgement, lease, result, and presence."""

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
            normalized_operation = str(operation or "").strip()
            if not normalized_operation or len(normalized_operation) > 64:
                raise ValueError("operation must be between 1 and 64 characters")
            if "*" in normalized_operation and not normalized_operation.endswith(".*"):
                raise ValueError(
                    "operation wildcard is supported only as a trailing .* suffix"
                )
            if normalized_operation in self._handlers:
                raise ValueError(
                    f"handler already registered for {normalized_operation}"
                )
            self._handlers[normalized_operation] = registration
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

    async def enforce_local_action(
        self,
        *,
        action: str,
        correlation_id: str,
        facts: Mapping[str, Any],
    ) -> None:
        """Apply the explicitly selected free local control source.

        Managed cluster control is already represented by the validated,
        target-bound delivery capability and is never replaced or retried here.
        """
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

    def register_handler(
        self,
        operation: str,
        handler: ConnectedHandler,
        *,
        consequential: bool,
        idempotency_mode: str = "none",
    ) -> None:
        normalized = str(operation or "").strip()
        if not normalized or len(normalized) > 64:
            raise ValueError("operation must be between 1 and 64 characters")
        if "*" in normalized and not normalized.endswith(".*"):
            raise ValueError("operation wildcard is supported only as a trailing .* suffix")
        if normalized in self._handlers:
            raise ValueError(f"handler already registered for {normalized}")
        self._handlers[normalized] = ConnectedOperationHandler(
            handler=handler,
            consequential=bool(consequential),
            idempotency_mode=idempotency_mode,
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
                    "instance_id",
                    "protocol_version",
                    "presence_status",
                    "registered_at",
                    "receive_path",
                    "heartbeat_path",
                    "drain_path",
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
            "PUT",
            self.config.mcp_descriptor_path_template,
            json={
                "protocol_version": self.config.protocol_version,
                "manifest": self._mcp_manifest,
                "expected_previous_manifest_hash": None,
            },
        )
        _strict_object(
            response.json(),
            {
                "integration_id",
                "manifest_hash",
                "descriptor_revision",
                "tool_count",
                "published_at",
            },
            "MCP descriptor response",
        )

    async def _ensure_registered(self) -> None:
        if self._instance_id is None and not self._stop_event.is_set():
            await self._register()

    async def _acknowledge(
        self,
        message: ConnectedMessage,
        acknowledgement: str,
        reason_code: Optional[str] = None,
    ) -> None:
        response = await self._request(
            "POST",
            self.config.acknowledgement_path_template,
            message=message,
            json={
                "lease_id": message.lease.lease_id,
                "lease_token": message.lease.lease_token,
                "acknowledgement": acknowledgement,
                "reason_code": reason_code,
            },
        )
        payload = _strict_object(
            response.json(),
            {"message_id", "lease_id", "acknowledgement", "acknowledged_at"},
            "acknowledgement response",
        )
        if (
            str(payload.get("message_id")) != message.message_id
            or str(payload.get("lease_id")) != message.lease.lease_id
            or payload.get("acknowledgement") != acknowledgement
        ):
            raise ConnectedProtocolError("acknowledgement response binding mismatch")

    async def _renew_lease(
        self, message: ConnectedMessage, context: _DeliveryActionContext
    ) -> None:
        expires_at = message.lease.expires_at
        while True:
            remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
            await asyncio.sleep(max(1.0, min(20.0, remaining / 2.0)))
            response = await self._request(
                "POST",
                self.config.lease_renewal_path_template,
                message=message,
                json={
                    "lease_id": message.lease.lease_id,
                    "lease_token": message.lease.lease_token,
                },
            )
            payload = _strict_object(
                response.json(),
                {"lease_id", "expires_at", "capability"},
                "lease renewal response",
            )
            if str(payload.get("lease_id")) != message.lease.lease_id:
                raise ConnectedProtocolError("renewed lease identity mismatch")
            renewed_capability = str(payload.get("capability") or "")
            await self._validator.validate_token(message, renewed_capability)
            try:
                expires_at = datetime.fromisoformat(
                    str(payload.get("expires_at") or "").replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ConnectedProtocolError("renewed lease expiry is invalid") from exc
            context.capability = renewed_capability

    async def _commit_result(
        self, message: ConnectedMessage, result: ConnectedHandlerResult
    ) -> None:
        response = await self._request(
            "POST",
            self.config.result_path_template,
            message=message,
            json={
                "lease_id": message.lease.lease_id,
                "lease_token": message.lease.lease_token,
                "terminal_status": result.terminal_status,
                "result_schema": result.result_schema,
                "result_payload": result.result_payload,
                "evidence_payload": result.evidence_payload,
            },
        )
        payload = _strict_object(
            response.json(),
            {"message_id", "result_id", "terminal_status", "committed_at"},
            "result response",
        )
        if (
            str(payload.get("message_id")) != message.message_id
            or payload.get("terminal_status") != result.terminal_status
        ):
            raise ConnectedProtocolError("result response binding mismatch")

    async def _invoke_handler(
        self,
        handler: ConnectedHandler,
        message: ConnectedMessage,
        actions: ConnectedActionClient,
    ) -> ConnectedHandlerResult:
        result = handler(message.delivery(), actions)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, ConnectedHandlerResult):
            raise TypeError("connected handler must return ConnectedHandlerResult")
        return result

    async def _process(self, message: ConnectedMessage, handler: ConnectedHandler) -> None:
        async with self._semaphore:
            context = _DeliveryActionContext(
                config=self.config,
                session=self.session,
                instance_id=str(self._instance_id),
                message=message,
                capability=message.capability,
            )
            actions = ConnectedActionClient(context)
            handler_task = asyncio.create_task(
                self._invoke_handler(handler, message, actions)
            )
            renewal_task = asyncio.create_task(self._renew_lease(message, context))
            try:
                done, _pending = await asyncio.wait(
                    {handler_task, renewal_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if renewal_task in done:
                    renewal_task.result()
                    raise ConnectedProtocolError("lease renewal stopped unexpectedly")
                result = handler_task.result()
            except asyncio.CancelledError:
                handler_task.cancel()
                await asyncio.gather(handler_task, return_exceptions=True)
                raise
            except Exception as exc:
                if not handler_task.done():
                    handler_task.cancel()
                    await asyncio.gather(handler_task, return_exceptions=True)
                logger.exception("connected handler failed for %s", message.operation)
                result = ConnectedHandlerResult(
                    terminal_status="failed",
                    result_schema="atellagent.connected.error.v1",
                    result_payload={"error_type": type(exc).__name__},
                )
            finally:
                renewal_task.cancel()
                await asyncio.gather(renewal_task, return_exceptions=True)
            await self._commit_result(message, result)

    def _track_delivery(self, task: asyncio.Task[Any]) -> None:
        self._delivery_tasks.add(task)

        def _finished(done: asyncio.Task[Any]) -> None:
            self._delivery_tasks.discard(done)
            if done.cancelled():
                return
            try:
                done.result()
            except Exception:
                logger.exception("connected delivery failed after acknowledgement")

        task.add_done_callback(_finished)

    async def _wait_for_other_deliveries(self, timeout: float = 30.0) -> None:
        current = asyncio.current_task()
        pending = {
            task
            for task in self._delivery_tasks
            if task is not current and not task.done()
        }
        if not pending:
            return
        _done, remaining = await asyncio.wait(pending, timeout=max(0.0, timeout))
        if remaining:
            raise ConnectedProtocolError(
                "certificate rotation could not drain active deliveries"
            )

    async def _drain_for_certificate_rotation(self) -> None:
        response = await self._request(
            "POST",
            self.config.drain_path_template,
            json={"mode": "graceful"},
        )
        payload = _strict_object(
            response.json(),
            {"instance_id", "presence_status", "drain_requested_at"},
            "rotation drain response",
        )
        if (
            str(payload.get("instance_id")) != self._instance_id
            or payload.get("presence_status") != "draining"
        ):
            raise ConnectedProtocolError("rotation drain response binding mismatch")

    async def _install_activated_rotation(self, staged: Any) -> None:
        old_session = self.session
        commit_staged_certificate_rotation(staged)
        self.session = GatewaySession.from_service_account_config(self.config)
        self._validator = ConnectedCapabilityValidator(self.config, self.session)
        self._instance_id = None
        await old_session.close_async()
        await self._register()

    async def _perform_certificate_rotation(
        self,
        message: ConnectedMessage,
    ) -> ConnectedHandlerResult:
        payload = _strict_object(
            message.payload,
            {
                "schema_version",
                "reason",
                "current_certificate_public_key_sha256",
                "due_at",
                "deadline_at",
            },
            "certificate rotation control payload",
        )
        if payload.get("schema_version") != "v1":
            raise ConnectedProtocolError("certificate rotation schema is unsupported")
        current_fingerprint = certificate_public_key_sha256(str(self.config.cert_path))
        if payload.get("current_certificate_public_key_sha256") != current_fingerprint:
            raise ConnectedProtocolError(
                "certificate rotation current identity binding mismatch"
            )
        try:
            deadline = datetime.fromisoformat(
                str(payload.get("deadline_at") or "").replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ConnectedProtocolError(
                "certificate rotation deadline is invalid"
            ) from exc
        if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
            raise ConnectedProtocolError("certificate rotation deadline has expired")

        await self._wait_for_other_deliveries()
        await self._drain_for_certificate_rotation()
        prepared = prepare_certificate_rotation(
            service_account_id=str(self.config.service_account_id),
            tenant_id=str(self.config.tenant_id),
            certificate_path=str(self.config.cert_path),
            private_key_path=str(self.config.key_path),
        )
        response = await self._rotation_request(
            "POST",
            self.config.certificate_rotation_path_template,
            json={"csr_pem": prepared.csr_pem},
        )
        begin = _strict_object(
            response.json(),
            {"operation_id", "status", "operation_path", "activation_path"},
            "certificate rotation response",
        )
        operation_id = str(begin.get("operation_id") or "").strip()
        if not operation_id:
            raise ConnectedProtocolError("certificate rotation has no operation id")

        delay = 0.5
        issued = None
        while datetime.now(timezone.utc) < deadline:
            response = await self._rotation_request(
                "GET",
                self.config.certificate_rotation_operation_path_template,
                operation_id=operation_id,
            )
            status_payload = _strict_object(
                response.json(),
                {"operation_id", "status", "last_error", "certificate"},
                "certificate rotation status",
            )
            if str(status_payload.get("operation_id")) != operation_id:
                raise ConnectedProtocolError(
                    "certificate rotation operation binding mismatch"
                )
            operation_status = str(status_payload.get("status") or "")
            if operation_status == "issued":
                certificate = status_payload.get("certificate")
                if not isinstance(certificate, Mapping):
                    raise ConnectedProtocolError(
                        "issued certificate rotation has no certificate"
                    )
                issued = dict(certificate)
                break
            if operation_status == "failed":
                raise ConnectedProtocolError("certificate rotation issuance failed")
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, 5.0)
        if issued is None:
            raise ConnectedProtocolError("certificate rotation issuance timed out")

        staged = stage_certificate_rotation(
            prepared,
            certificate_pem=str(issued.get("certificate_pem") or ""),
            certificate_chain_pem=str(issued.get("certificate_chain_pem") or ""),
        )
        activated = False
        try:
            response = await self._rotation_request(
                "POST",
                self.config.certificate_rotation_activation_path_template,
                operation_id=operation_id,
                json={
                    "message_id": message.message_id,
                    "lease_id": message.lease.lease_id,
                    "lease_token": message.lease.lease_token,
                },
            )
            activation = _strict_object(
                response.json(),
                {
                    "operation_id",
                    "status",
                    "certificate_public_key_sha256",
                    "certificate_expires_at",
                    "certificate_rotation_due_at",
                    "certificate_rotation_deadline_at",
                },
                "certificate rotation activation response",
            )
            if (
                str(activation.get("operation_id")) != operation_id
                or activation.get("status") != "activated"
            ):
                raise ConnectedProtocolError(
                    "certificate rotation activation binding mismatch"
                )
            activated = True
            try:
                await self._install_activated_rotation(staged)
            except Exception as exc:
                raise _CertificateRotationActivatedError(
                    "certificate activated but local reconnect failed; use recovery enrollment"
                ) from exc
        finally:
            if not activated:
                discard_staged_certificate_rotation(staged)

        return ConnectedHandlerResult.succeeded(
            result_schema="atellagent.connected.certificate-rotation-result.v1",
            result_payload={
                "operation_id": operation_id,
                "status": "reconnected",
                "certificate_expires_at": staged.certificate_expires_at.isoformat(),
            },
        )

    async def _process_certificate_rotation(self, message: ConnectedMessage) -> None:
        context = _DeliveryActionContext(
            config=self.config,
            session=self.session,
            instance_id=str(self._instance_id),
            message=message,
            capability=message.capability,
        )
        renewal_task = asyncio.create_task(self._renew_lease(message, context))
        failure_result: Optional[ConnectedHandlerResult] = None
        try:
            async with self._rotation_lock:
                await self._perform_certificate_rotation(message)
        except _CertificateRotationActivatedError:
            logger.exception(
                "certificate rotation activated but the new identity did not reconnect"
            )
        except Exception as exc:
            logger.exception("supervised certificate rotation failed")
            try:
                self._instance_id = None
                await self._register()
            except Exception:
                logger.exception(
                    "certificate rotation failure could not restore receiver presence"
                )
            failure_result = ConnectedHandlerResult(
                terminal_status="failed",
                result_schema="atellagent.connected.certificate-rotation-error.v1",
                result_payload={"error_type": type(exc).__name__},
            )
        finally:
            renewal_task.cancel()
            await asyncio.gather(renewal_task, return_exceptions=True)
        try:
            if failure_result is not None:
                await self._commit_result(message, failure_result)
        finally:
            self._receive_enabled.set()

    async def _receive_once(self) -> None:
        await self._ensure_registered()
        response = await self._request(
            "POST",
            self.config.receive_path_template,
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
                await self._acknowledge(
                    message, "rejected", "unsupported_control_operation"
                )
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
            self._track_delivery(
                asyncio.create_task(self._process_certificate_rotation(message))
            )
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
        self._track_delivery(
            asyncio.create_task(self._process(message, registration.handler))
        )

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
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.heartbeat_interval
                )
                continue
            except asyncio.TimeoutError:
                pass
            try:
                await self._receive_enabled.wait()
                await self._ensure_registered()
                certificate = x509.load_pem_x509_certificate(
                    Path(str(self.config.cert_path)).read_bytes()
                )
                response = await self._request(
                    "POST",
                    self.config.heartbeat_path_template,
                    json={
                        "protocol_version": self.config.protocol_version,
                        "capabilities": self.config.capabilities,
                        "certificate_public_key_sha256": (
                            certificate_public_key_sha256(str(self.config.cert_path))
                        ),
                        "certificate_expires_at": (
                            certificate.not_valid_after_utc.isoformat()
                        ),
                    },
                )
                payload = _strict_object(
                    response.json(),
                    {"instance_id", "presence_status", "heartbeat_at"},
                    "heartbeat response",
                )
                if str(payload.get("instance_id")) != self._instance_id:
                    raise ConnectedProtocolError("heartbeat response binding mismatch")
            except ConnectedHTTPError as exc:
                if exc.status_code in {403, 404}:
                    self._instance_id = None
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
            raise RuntimeError(
                "credential reload requires a participant-owned GatewaySession"
            )
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
            task.cancel()
        await asyncio.gather(*self._loop_tasks, return_exceptions=True)
        if self._delivery_tasks:
            _done, pending = await asyncio.wait(
                set(self._delivery_tasks), timeout=max(0.0, float(grace_seconds))
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        if self._instance_id:
            try:
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
        if self._owns_session:
            await self.session.close_async()

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
