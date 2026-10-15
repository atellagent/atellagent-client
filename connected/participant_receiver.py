# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Connected message receive loop and inbound dispatch."""

from __future__ import annotations

import asyncio
import logging
import random


from .contracts import (
    ConnectedProtocolError,
    parse_connected_message,
)
from .participant_protocol import ConnectedHTTPError, strict_object as _strict_object

logger = logging.getLogger(__name__)


class ConnectedReceiverMixin:
    async def _receive_once(self) -> None:
        # Serialize claim/publication, including direct concurrent receive calls.
        # Pending work stays in the durable gateway queue instead of becoming
        # unrenewed local tasks waiting behind the handler semaphore.
        async with self._receive_lock:
            await self._receive_enabled.wait()
            while not self._stop_event.is_set():
                active = {task for task in self._delivery_tasks if not task.done()}
                if len(active) < self._max_concurrency:
                    break
                await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            if self._stop_event.is_set():
                return
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
