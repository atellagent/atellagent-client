# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Device-owned reporting lifecycle independent of any native controllers."""

import asyncio
from pathlib import Path

from .device import DeviceManagementClient
from .device_installation import DeviceInstallation
from .device_reporter import DeviceReporter
from .device_renewal_loop import DeviceRenewalLoop
from .device_renewal_state import DeviceRenewalState
from .device_renewal_loading import retained_device_certificate_metadata
from .device_resume import resume_stored_device
from .device_trust import fetch_device_trust
from .reporting_state import DeviceReportingStore
from .reporting_scope import ReportingScope
from .process_lease import process_lease
from .errors import OnboardingClientError


class DeviceRuntime:
    def __init__(self, client, reporter, renewal, lease):
        self.client, self.reporter, self.renewal = client, reporter, renewal
        self.lease = lease
        self.stop_event = asyncio.Event()
        self.task = None
        self.renewal_task = None

    @classmethod
    async def open(cls, directory: Path):
        installation = DeviceInstallation(directory)
        lease = process_lease(directory.parent / ".device-runtime.lock")
        if not lease.__enter__():
            lease.__exit__(None, None, None)
            raise OnboardingClientError("device_runtime_already_running")
        client = None
        try:
            draft, receipt, fingerprint = await asyncio.to_thread(installation.load)
            trust = await fetch_device_trust(draft.platform_origin)
            activated = await resume_stored_device(draft=draft, receipt=receipt, trust=trust,
                setup_store=installation.setups, credential_store=installation.credentials, fingerprint=fingerprint)
            client = DeviceManagementClient(origin=trust.device_api_origin, device=activated.device)
            store = DeviceReportingStore(directory / "reporting", ReportingScope.from_device(activated.device))
            if not store.initialize(reporting_enabled=activated.reporting_enabled):
                raise ValueError("device reporting state is busy")
            renewal_state = DeviceRenewalState(directory, ReportingScope.from_device(activated.device))
            material = await asyncio.to_thread(retained_device_certificate_metadata, device=activated.device, trust=trust)
            renewal_state.clear_if_replaced(material.certificate_sha256)
            return cls(client, DeviceReporter(client, store), DeviceRenewalLoop(client, trust, renewal_state), lease)
        except BaseException:
            try:
                if client is not None:
                    await client.aclose()
            finally:
                lease.__exit__(None, None, None)
            raise

    async def __aenter__(self):
        if self.task is not None or self.stop_event.is_set():
            raise ValueError("device runtime cannot be started twice")
        self.task = asyncio.create_task(self.reporter.run(self.stop_event), name="device-discovery-reporter")
        self.renewal_task = asyncio.create_task(self.renewal.run(self.stop_event), name="device-certificate-renewal")
        return self

    async def __aexit__(self, *_args):
        self.stop_event.set()
        tasks = [task for task in (self.task, self.renewal_task) if task is not None]
        for task in tasks:
            task.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            try:
                await self.client.aclose()
            finally:
                self.lease.__exit__(None, None, None)
