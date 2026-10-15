# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Native-hook telemetry validation and connected heartbeat reporting."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from cryptography import x509


from .capability import certificate_public_key_sha256
from .contracts import (
    ConnectedProtocolError,
)
from .participant_protocol import ConnectedHTTPError, strict_object as _strict_object

logger = logging.getLogger(__name__)


class ConnectedHealthMixin:
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

    def set_native_hook_event_receipts(
        self, *, event_receipts: list[dict[str, object]]
    ) -> None:
        """Publish bounded local hook-health receipts on the next heartbeat."""

        # Claude Code has four supported lifecycle checkpoints, including its
        # distinct PostToolUseFailure callback. Keep one receipt per event so
        # its healthy-boundary report is complete and still fixed-cardinality.
        if len(event_receipts) > 4:
            raise ValueError(
                "native hook event receipt count exceeds the accepted limit"
            )
        normalized: list[dict[str, object]] = []
        for receipt in event_receipts:
            if not isinstance(receipt, dict):
                raise ValueError("native hook event receipt must be an object")
            normalized.append(dict(receipt))
        self._native_hook_event_receipts = normalized

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
                heartbeat_payload = {
                    "protocol_version": self.config.protocol_version,
                    "capabilities": self.config.capabilities,
                    "certificate_public_key_sha256": certificate_public_key_sha256(
                        str(self.config.cert_path)
                    ),
                    "certificate_expires_at": certificate.not_valid_after_utc.isoformat(),
                }
                # Native-hook coverage is optional telemetry, not part of a
                # connected runtime's base liveness protocol. The gateway
                # establishes whether this identity is a reviewed native host
                # by returning a signed posture on an ordinary heartbeat.
                if self._native_hook_posture:
                    heartbeat_payload["native_hook_coverage_health"] = {
                        "observe_offline_permits": self._native_hook_observe_offline_permits,
                        "event_receipts": self._native_hook_event_receipts,
                    }
                response = await self._request(
                    "POST",
                    self.config.heartbeat_path_template,
                    json=heartbeat_payload,
                )
                payload = _strict_object(
                    response.json(),
                    {
                        "instance_id",
                        "presence_status",
                        "heartbeat_at",
                        "native_hook_posture",
                    },
                    "heartbeat response",
                )
                if str(payload.get("instance_id")) != self._instance_id:
                    raise ConnectedProtocolError("heartbeat response binding mismatch")
                posture = payload.get("native_hook_posture")
                if posture is not None and not isinstance(posture, str):
                    raise ConnectedProtocolError(
                        "native hook posture response is invalid"
                    )
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
