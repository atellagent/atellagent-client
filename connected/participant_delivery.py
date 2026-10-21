# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Delivery acknowledgement, lease renewal, and handler execution helpers."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .actions import ConnectedActionClient, _DeliveryActionContext
from .contracts import ConnectedHandlerResult, ConnectedMessage, ConnectedProtocolError
from .participant_protocol import ConnectedHTTPError, strict_object
from .result_budget import build_result_commit_payload


logger = logging.getLogger(__name__)
_RESULT_COMMIT_TIMEOUT_SECONDS = 30.0
_RESULT_COMMIT_MAX_ATTEMPTS = 6


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
        # Snapshot once: an uncertain response may already have committed this
        # exact result. Retry only its idempotent message/lease-bound POST, never
        # the handler or a newly constructed outcome.
        commit_payload = json.loads(json.dumps(
            build_result_commit_payload(message, result), allow_nan=False,
        ))
        try:
            async with asyncio.timeout(_RESULT_COMMIT_TIMEOUT_SECONDS):
                for attempt in range(_RESULT_COMMIT_MAX_ATTEMPTS):
                    try:
                        response = await self._request(
                            "POST",
                            self.config.result_path_template,
                            message=message,
                            json=commit_payload,
                        )
                        break
                    except ConnectedHTTPError as exc:
                        if exc.status_code not in {502, 503, 504}:
                            raise
                        if attempt + 1 == _RESULT_COMMIT_MAX_ATTEMPTS:
                            raise
                    except (httpx.NetworkError, httpx.TimeoutException,
                            httpx.RemoteProtocolError):
                        if attempt + 1 == _RESULT_COMMIT_MAX_ATTEMPTS:
                            raise
                    await asyncio.sleep(min(2.0, 0.25 * (2 ** attempt)))
        except Exception as exc:
            logger.warning(
                "Connected result commit unconfirmed (message_id=%s, error_type=%s)",
                message.message_id, type(exc).__name__,
                extra={"connected_message_id": message.message_id,
                       "connected_operation": message.operation},
            )
            raise
        payload = strict_object(
            response.json(),
            {"message_id", "result_id", "terminal_status", "committed_at"},
            "result response",
        )
        if (
            str(payload.get("message_id")) != message.message_id
            or payload.get("terminal_status") != commit_payload["terminal_status"]
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
            commit_task = None
            try:
                done, _pending = await asyncio.wait(
                    {handler_task, renewal_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if renewal_task in done:
                    renewal_task.result()
                    raise ConnectedProtocolError("lease renewal stopped unexpectedly")
                try:
                    result = handler_task.result()
                except Exception as exc:
                    logger.exception("connected handler failed for %s", message.operation)
                    result = ConnectedHandlerResult(
                        terminal_status="failed",
                        result_schema="atellagent.connected.error.v1",
                        result_payload={"error_type": type(exc).__name__},
                    )
                # The delivery still owns its slot and renewal until the result
                # is acknowledged. Lease loss cancels the commit, not the effect.
                commit_task = asyncio.create_task(self._commit_result(message, result))
                done, _pending = await asyncio.wait(
                    {commit_task, renewal_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if commit_task in done:
                    await commit_task
                else:
                    try:
                        renewal_task.result()
                    except ConnectedHTTPError as exc:
                        if exc.status_code != 409:
                            raise
                        # The result may have committed before its reply arrives,
                        # making renewal correctly reject the terminal lease.
                        # Only the already-started, bounded exact-result commit
                        # can establish success. Never rerun the handler or
                        # accept the renewal conflict as a result receipt.
                        await commit_task
                    else:
                        raise ConnectedProtocolError("lease renewal stopped unexpectedly")
            finally:
                children = [handler_task, renewal_task]
                if commit_task is not None:
                    children.append(commit_task)
                for child in children:
                    if not child.done():
                        child.cancel()
                await asyncio.gather(*children, return_exceptions=True)

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
