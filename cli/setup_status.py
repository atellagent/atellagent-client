"""Structured read-only onboarding progress for native desktop consumers."""

import argparse
import json

from atellagent_client.onboarding.installation_paths import installation_directory
from atellagent_client.onboarding.contract_values import canonical_id
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.setup_overview import overview


async def run(arguments):
    parser = argparse.ArgumentParser(prog="atellagent-cli setup status")
    parser.add_argument("--json", action="store_true", required=True)
    parser.add_argument("--after", help="Continue a device assignment page using its exact returned cursor")
    args = parser.parse_args(arguments)
    try:
        after = None if args.after is None else canonical_id(args.after)
        result = await overview(installation_directory(), after=after)
        print(json.dumps(result, sort_keys=True))
    except (OnboardingClientError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, OnboardingClientError) else "onboarding_status_unavailable"
        parser.exit(1, f"[ERROR] {code}\n")
