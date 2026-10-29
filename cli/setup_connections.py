"""Thin desktop lifecycle shell; local enablement is explicit, provisioning is not."""

import argparse
import json
from uuid import UUID

from atellagent_client.onboarding.installation_paths import installation_directory
from atellagent_client.sdk import NativeSetupContext, DeviceMCPContext, reconcile_connections, OnboardingClientError


async def run(arguments):
    parser = argparse.ArgumentParser(description="Prepare approved connections or explicitly enable/disable one.")
    parser.add_argument("action", choices=("reconcile", "enable", "disable"))
    parser.add_argument("kind", nargs="?", choices=("native", "mcp"))
    parser.add_argument("request_id", nargs="?", type=UUID)
    args = parser.parse_args(arguments)
    if args.action != "reconcile" and (args.kind is None or args.request_id is None):
        parser.error("An exact connection kind and request ID are required")
    try:
        await execute(args)
    except (OnboardingClientError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, OnboardingClientError) else "local_connection_setup_unavailable"
        import sys
        print(json.dumps(dict(error=code)), file=sys.stderr, flush=True)
        raise SystemExit(1) from None


async def execute(args):
    directory = installation_directory()
    if args.action == "reconcile":
        print(json.dumps(await reconcile_connections(directory)), flush=True)
        return
    if args.kind == "native":
        from .setup_native_hooks import preview, apply
        if args.action == "enable":
            async with await NativeSetupContext.open(directory) as context:
                await context.select(args.request_id)
            try:
                review = preview(directory, args.request_id)
                apply(directory, args.request_id, review["review_sha256"])
            except Exception as exc:
                # Do not leave a newly enabled controller on after a config conflict.
                # If a write committed, retain its controller so hooks are not orphaned.
                if not getattr(exc, "written", ()):
                    NativeSetupContext.stop_selected(directory, args.request_id)
                raise
        else:
            review = preview(directory, args.request_id, remove=True)
            apply(directory, args.request_id, review["review_sha256"], remove=True)
            NativeSetupContext.stop_selected(directory, args.request_id)
    elif args.action == "enable":
        async with await DeviceMCPContext.open(directory) as context:
            review = context.review(args.request_id)
            await context.select(args.request_id, reviewed_local_inputs_sha256=review["local_inputs_sha256"])
    else:
        DeviceMCPContext.stop_selected(directory, args.request_id)
    print(json.dumps(dict(request_id=str(args.request_id), enabled=args.action == "enable")), flush=True)
