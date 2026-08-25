# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Delivery acknowledgement, lease renewal, and handler execution helpers."""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timezone
from typing import Any

from .actions import ConnectedActionClient, _DeliveryActionContext
from .contracts import ConnectedHandlerResult, ConnectedMessage, ConnectedProtocolError
from .participant_protocol import strict_object


logger = logging.getLogger(__name__)


class ConnectedDeliveryMixin:
    """Operations owned by a participant after a delivery is accepted."""

    async def _acknowledge(
        self,
        message: ConnectedMessage,
        acknowledgement: str,
        reason_code: str | None = None,
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
        payload = strict_object(
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
            payload = strict_object(
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
        payload = strict_object(
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
        handler: Any,
        message: ConnectedMessage,
        actions: ConnectedActionClient,
    ) -> ConnectedHandlerResult:
        result = handler(message.delivery(), actions)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, ConnectedHandlerResult):
            raise TypeError("connected handler must return ConnectedHandlerResult")
        return result

    async def _process(self, message: ConnectedMessage, handler: Any) -> None:
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


__all__ = ["ConnectedDeliveryMixin"]
