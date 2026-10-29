"""Verified device context shared below native/MCP consumers and every shell."""

import asyncio
from pathlib import Path

from .device import DeviceManagementClient
from .device_installation import DeviceInstallation
from .device_resume import resume_stored_device
from .device_trust import fetch_device_trust


class DeviceContext:
    def __init__(self, installation, setup, client):
        self.installation, self.setup, self.client = installation, setup, client

    @classmethod
    async def open(cls, directory):
        installation = DeviceInstallation(Path(directory))
        setup, receipt, fingerprint = await asyncio.to_thread(installation.load)
        trust = await fetch_device_trust(setup.platform_origin)
        activated = await resume_stored_device(draft=setup, receipt=receipt, trust=trust,
            setup_store=installation.setups, credential_store=installation.credentials, fingerprint=fingerprint)
        client = DeviceManagementClient(origin=trust.device_api_origin, device=activated.device)
        return cls(installation, setup, client)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.client.aclose()
