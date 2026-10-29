"""Read-only recovery inventory; server failures never mean not submitted."""

import argparse
import json

from atellagent_client.onboarding.contract_values import canonical_id
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_saved_inventory import page
from atellagent_client.onboarding.native_setup_context import NativeSetupContext
from atellagent_client.onboarding.installation_paths import installation_directory


async def run(arguments):
    parser = argparse.ArgumentParser(prog="atellagent-cli setup native saved")
    parser.add_argument("--json", action="store_true", required=True)
    parser.add_argument("--after")
    args = parser.parse_args(arguments)
    try:
        after = None if args.after is None else canonical_id(args.after)
        async with await NativeSetupContext.open(installation_directory()) as context:
            result = await page(context, after=after)
        print(json.dumps(result, sort_keys=True))
    except (OnboardingClientError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, OnboardingClientError) else "onboarding_saved_inventory_unavailable"
        parser.exit(1, f"[ERROR] {code}\n")
