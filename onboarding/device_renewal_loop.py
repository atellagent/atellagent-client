# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""One device renewal worker, independent of reporting and human sign-in."""

import asyncio
from datetime import datetime, timezone
import logging

from atellagent_client.sdk.certificate_lifecycle import certificate_renewal_due_at
from .device_renewal_loading import retained_device_certificate_metadata
from .errors import OnboardingClientError


log = logging.getLogger(__name__)


class DeviceRenewalLoop:
    def __init__(self, client, trust, state):
        if client.origin != trust.device_api_origin:
            raise OnboardingClientError("device_renewal_origin_mismatch")
        self.client, self.trust = client, trust
        self.state = state

    async def tick(self):
        material = await asyncio.to_thread(retained_device_certificate_metadata,
            device=self.client.device, trust=self.trust)
        if datetime.now(timezone.utc) < certificate_renewal_due_at(material.not_before, material.expires_at):
            return False
        # The protocol rechecks current material and server authority. Historical
        # expiry here merely schedules recovery; it cannot authorize parent TLS.
        await self.client.renew_certificate(trust=self.trust)
        material = await asyncio.to_thread(retained_device_certificate_metadata, device=self.client.device, trust=self.trust)
        await asyncio.to_thread(self.state.save, None, certificate_sha256=material.certificate_sha256)
        return True

    async def run(self, stop):
        while not stop.is_set():
            try:
                await self.tick()
            except OnboardingClientError as exc:
                log.warning("device_renewal_failed code=%s", exc.code)
                reason = "device_renewal_recovery_required" if exc.code == "device_renewal_recovery_required" else "device_renewal_retrying"
                await self._save_warning(reason)
                if exc.code == "device_renewal_recovery_required":
                    log.warning("device_renewal_recovery_required action=approved_device_recovery")
                    # Retain the exact proposal and existing authority. Terminal
                    # work cannot become live through retry, login or a new key.
                    await stop.wait()
                    return
            except (OSError, ValueError):
                log.warning("device_renewal_failed code=device_renewal_state_unavailable")
                await self._save_warning("device_renewal_retrying")
            try:
                await asyncio.wait_for(stop.wait(), timeout=300)
            except TimeoutError:
                pass

    async def _save_warning(self, reason):
        try:
            material = await asyncio.to_thread(retained_device_certificate_metadata,
                device=self.client.device, trust=self.trust)
            await asyncio.to_thread(self.state.save, reason, certificate_sha256=material.certificate_sha256)
        except (OSError, ValueError, OnboardingClientError):
            log.warning("device_renewal_failed code=device_renewal_status_storage_unavailable")
