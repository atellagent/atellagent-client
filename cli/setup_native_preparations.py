"""Read-only signed preparation inventory, independent of management sign-in."""

import argparse
import json

from atellagent_client.onboarding.contract_values import canonical_id
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_setup_context import NativeSetupContext
from atellagent_client.onboarding.installation_paths import installation_directory


async def run(arguments):
    parser = argparse.ArgumentParser(prog="atellagent-cli setup native preparations")
    parser.add_argument("--json", action="store_true", required=True)
    selectors = parser.add_mutually_exclusive_group()
    selectors.add_argument("--after")
    selectors.add_argument("--assignment-id")
    args = parser.parse_args(arguments)
    try:
        after = None if args.after is None else canonical_id(args.after)
        assignment_id = None if args.assignment_id is None else canonical_id(args.assignment_id)
        async with await NativeSetupContext.open(installation_directory()) as context:
            page = await context.client.preparations(after=after, assignment_id=assignment_id)
        print(json.dumps(dict(preparations=[item.payload() for item in page.preparations],
            next_after=None if page.next_after is None else str(page.next_after)), sort_keys=True))
    except (OnboardingClientError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, OnboardingClientError) else "onboarding_preparation_inventory_unavailable"
        parser.exit(1, f"[ERROR] {code}\n")
