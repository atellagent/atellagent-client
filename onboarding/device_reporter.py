# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Independent reporting lifecycle; no controller, human token or login side effect."""

import asyncio
from dataclasses import replace
import logging
from time import time

from .device import DeviceManagementClient
from .errors import OnboardingClientError
from .reporting_state import DeviceReportingStore
from .reporting_scope import ReportingScope


log = logging.getLogger(__name__)


class DeviceReporter:
    def __init__(self, client: DeviceManagementClient, store: DeviceReportingStore):
        if ReportingScope.from_device(client.device) != store.scope:
            raise ValueError("reporter must retain exact device identity")
        self.client, self.store = client, store

    async def tick(self) -> bool:
        with self.store.lease() as held:
            if not held:
                return False
            state = self.store.load()
            if state.pending_sync:
                if state.enabled:
                    await self.client.set_reporting(True)
                else:
                    await self.client.remove_discovery()
                state = replace(state, pending_sync=False)
                self.store.save(state)
            if not state.enabled:
                return False
            now = time()
            if 0 <= now - state.uploaded_at < 300:
                return False
            # Administrator preference and revocation remain current authority.
            if (await self.client.status()).discovery_reporting_status != "enabled":
                return False
            await self.client.report_discovery()
            self.store.save(replace(state, uploaded_at=time()))
            return True

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            delay = 30
            try:
                await self.tick()
            except OnboardingClientError as exc:
                log.warning("device_reporting_failed code=%s", exc.code)
                delay = max(delay, exc.retry_after or 0)
            except (OSError, ValueError):
                log.warning("device_reporting_failed code=device_reporting_state_unavailable")
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
