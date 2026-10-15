"""Explicit exact-device request cancellation; no automatic replacement setup."""

import argparse

from atellagent_client.onboarding.contract_values import canonical_id
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.pending_device_cancellation import cancel_pending_device
from atellagent_client.onboarding.installation_paths import installation_directory


async def run(arguments):
    parser = argparse.ArgumentParser(prog="atellagent-cli setup cancel",
        description="Cancel this unfinished device request and retain its local state in a private archive.")
    parser.add_argument("--confirm-setup", required=True, help="Exact pending setup ID from setup status")
    args = parser.parse_args(arguments)
    try:
        archive = await cancel_pending_device(installation_directory(),
            expected_setup_id=canonical_id(args.confirm_setup))
        print(f"Device request ended; local keys and state retained in: {archive}\n"
              "You can request a new device approval. Already enrolled devices and connected integrations are unchanged.")
    except (OnboardingClientError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, OnboardingClientError) else "device_archive_unavailable"
        parser.exit(1, f"[ERROR] {code}; cancellation/archive was not confirmed. Refresh setup before retrying.\n")
