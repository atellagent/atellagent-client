"""Command entry point for the local controller supervisor."""

import logging

from atellagent_client.onboarding.installation_paths import installation_directory
from atellagent_client.runtime.supervisor import run as run_supervisor
from . import service_platform as service
from .shutdown import wait_for_shutdown_signal


async def run() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.WARNING,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await run_supervisor(installation_directory(), endpoint=service._endpoint,
                         wait_for_shutdown=wait_for_shutdown_signal)
