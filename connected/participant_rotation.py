# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Supervised certificate rotation for connected participants."""

from __future__ import annotations

import asyncio
import logging
import httpx
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from atellagent_client.sdk.certificate_rotation import (
    commit_staged_certificate_rotation,
    prepare_certificate_rotation,
    stage_certificate_rotation,
)
from atellagent_client.sdk.gateway.session import GatewaySession
from atellagent_client.onboarding.service_account_trust import fetch_service_account_trust
from atellagent_client.onboarding.contract_values import canonical_id
from atellagent_client.onboarding.errors import OnboardingClientError

from .actions import _DeliveryActionContext
from .capability import ConnectedCapabilityValidator, certificate_public_key_sha256
from .contracts import ConnectedHandlerResult, ConnectedMessage, ConnectedProtocolError
from .participant_protocol import ConnectedHTTPError, strict_object
from .rotation_recovery import recover_activated_rotation, validate_rotation_activation


logger = logging.getLogger(__name__)


class _CertificateRotationActivatedError(ConnectedProtocolError):
    """The cluster cutover committed, so the old delivery cannot be failed."""


class ConnectedCertificateRotationMixin:
    """Certificate-rotation control delivery implementation."""

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
        payload = strict_object(
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
        certificate_path, key_path = commit_staged_certificate_rotation(staged)
        self.config.cert_path, self.config.key_path = str(certificate_path), str(key_path)
        self.session = GatewaySession.from_service_account_config(self.config,
            device_proof_provider=self._device_proof_provider)
        self._validator = ConnectedCapabilityValidator(self.config, self.session)
        self._instance_id = None
        await old_session.close_async()
        await self._register()

    async def _activate_issued_rotation(
        self, message: ConnectedMessage, operation_id: str, deadline: datetime,
    ) -> Any:
        """Retry server errors only after confirming activation did not commit.

        Reuse the same operation, lease and staged key. Transport failures are
        ambiguous and must not trigger an automatic replay of the activation.
        """
        for attempt in range(3):
            try:
                return await self._rotation_request(
                    "POST",
                    self.config.certificate_rotation_activation_path_template,
                    operation_id=operation_id,
                    json={
                        "message_id": message.message_id,
                        "lease_id": message.lease.lease_id,
                        "lease_token": message.lease.lease_token,
                    },
                )
            except ConnectedHTTPError as exc:
                if exc.status_code < 500 or exc.status_code >= 600 or attempt == 2:
                    raise
                delay = 2 ** attempt
                if (deadline - datetime.now(timezone.utc)).total_seconds() <= delay:
                    raise
                response = await self._rotation_request(
                    "GET", self.config.certificate_rotation_operation_path_template,
                    operation_id=operation_id,
                )
                state = strict_object(
                    response.json(),
                    {"operation_id", "status", "last_error", "certificate"},
                    "certificate rotation retry status",
                )
                if str(state.get("operation_id")) != operation_id or state.get("status") != "issued":
                    raise
                logger.warning(
                    "Certificate activation returned HTTP %s; operation %s is still issued; retrying in %ss",
                    exc.status_code, operation_id, delay,
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable certificate activation retry state")

    async def _perform_certificate_rotation(
        self,
        message: ConnectedMessage,
    ) -> ConnectedHandlerResult:
        payload = strict_object(
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
            raise ConnectedProtocolError("certificate rotation deadline is invalid") from exc
        if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
            raise ConnectedProtocolError("certificate rotation deadline has expired")

        prepared = prepare_certificate_rotation(
            service_account_id=str(self.config.service_account_id),
            tenant_id=str(self.config.tenant_id),
            certificate_path=str(self.config.cert_path),
            private_key_path=str(self.config.key_path),
        )
        await self._wait_for_other_deliveries()
        await self._drain_for_certificate_rotation()
        response = await self._rotation_request(
            "POST",
            self.config.certificate_rotation_path_template,
            json={"csr_pem": prepared.csr_pem},
        )
        begin = strict_object(
            response.json(),
            {"operation_id", "status", "operation_path", "activation_path"},
            "certificate rotation response",
        )
        try:
            operation_id = str(canonical_id(begin.get("operation_id")))
        except (ValueError, TypeError) as exc:
            raise ConnectedProtocolError("certificate rotation has no canonical operation id") from exc

        delay = 0.5
        issued: Optional[dict[str, Any]] = None
        while datetime.now(timezone.utc) < deadline:
            response = await self._rotation_request(
                "GET",
                self.config.certificate_rotation_operation_path_template,
                operation_id=operation_id,
            )
            status_payload = strict_object(
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
            if operation_status in {"failed", "cancelled"}:
                raise ConnectedProtocolError("certificate rotation issuance " + operation_status)
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, 5.0)
        if issued is None:
            raise ConnectedProtocolError("certificate rotation issuance timed out")

        trust = await fetch_service_account_trust(self.config.platform_origin)
        staged = stage_certificate_rotation(
            prepared,
            certificate_pem=str(issued.get("certificate_pem") or ""),
            certificate_chain_pem=str(issued.get("certificate_chain_pem") or ""),
            trusted_ca_pem=trust.ca_bundle_pem,
            operation_id=operation_id,
        )
        # Missing activation acknowledgement is ambiguous: retain the only
        # replacement key for authoritative operation reconciliation.
        try:
            response = await self._activate_issued_rotation(message, operation_id, deadline)
            activation = strict_object(response.json(), {
                "operation_id", "status", "certificate_public_key_sha256",
                "certificate_expires_at", "certificate_rotation_due_at",
                "certificate_rotation_deadline_at"}, "certificate rotation activation response")
        except (httpx.TransportError, ConnectedProtocolError, ValueError):
            activation = await recover_activated_rotation(config=self.config, staged=staged,
                device_proof_provider=self._device_proof_provider)
        validate_rotation_activation(activation, staged=staged, operation_id=operation_id)
        try:
            await self._install_activated_rotation(staged)
        except Exception as exc:
            raise _CertificateRotationActivatedError(
                "certificate activated but local reconnect failed; reconcile the retained rotation"
            ) from exc

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
            retryable = (isinstance(exc, httpx.TransportError)
                or isinstance(exc, ConnectedHTTPError) and (exc.status_code == 429 or exc.status_code >= 500)
                or isinstance(exc, OnboardingClientError) and exc.retryable
                or isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500)
            if retryable:
                logger.warning("Retained rotation waits for a fresh delivery lease after a temporary failure")
            else:
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


__all__ = ["ConnectedCertificateRotationMixin"]
