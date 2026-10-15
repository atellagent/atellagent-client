"""Request and run a retry-safe echo agent using only supported SDK APIs.

Request:
  python -m atellagent_client.examples.onboarding --state-directory /absolute/private/state \
    --platform-url https://your-deployment.example --name "Echo agent" --capability CAPABILITY
Continue after administrator approval:
  python -m atellagent_client.examples.onboarding --state-directory /absolute/private/state \
    --request-id REQUEST_UUID

Use an agent capability supported by your deployment. Keep the state directory;
it holds the retained keys and approved connection, not just disposable settings.
"""

import argparse
import asyncio
from pathlib import Path
from uuid import UUID, uuid4

from atellagent_client.sdk import (
    DeviceLoginClient, SetupManagementClient, WorkloadContext, WorkloadLocalInputs,
    WorkloadProposal, setup_progress,
)


def echo(payload):
    """Pure handler: repeating an invocation has no external side effects."""
    return {"content": "Echo agent received the governed request."}


async def request(context, args):
    async with await DeviceLoginClient.open(args.platform_url) as login:
        challenge = await login.begin()
        print(f"Open {challenge.browser_url} (another device is fine). Code: {challenge.user_code}", flush=True)
        tokens = await login.wait()
    async with SetupManagementClient(origin=args.platform_url, tokens=tokens) as management:
        identity = await management.identity()
        for number, membership in enumerate(identity.memberships, 1):
            print(f"{number}: {membership.tenant_name}")
        selected = int(input("Choose organization number: "))
        if not 1 <= selected <= len(identity.memberships):
            raise ValueError("Choose a displayed organization")
        tenant = identity.memberships[selected - 1].tenant_id
        proposal = WorkloadProposal(uuid4(), args.name, "agent", "sdk", tuple(args.capability))
        # The example's pure handler is retry-safe. Real consequential handlers
        # must durably honor the delivery idempotency key before asserting this.
        inputs = WorkloadLocalInputs(deployment={"type": "sdk"}, target_idempotent=True)
        await context.prepare_request(management, proposal=proposal, inputs=inputs,
                                      tenant_id=tenant, owner_user_id=identity.user_id)
        print(f"Retained request ID: {proposal.id}", flush=True)
        await context.submit_request(management, proposal.id)
        return proposal.id


async def run(args):
    context = WorkloadContext(args.state_directory)
    request_id = args.request_id or await request(context, args)
    proposal = context.proposal(request_id)
    if proposal.account_type != "agent" or proposal.packaging != "sdk":
        raise ValueError("This example requires an SDK agent connection")
    async for progress in setup_progress(context.flow(request_id), seconds=60):
        print(progress.stage, flush=True)
        if progress.stage == "retained":
            print(f"Continue with --request-id {request_id}; administrator approval may still be pending.")
            return
    async with context.open_runtime(request_id, handler=echo) as runtime:
        print(f"Registered: {runtime.instance_id}. Press Ctrl+C to stop.", flush=True)
        await asyncio.Event().wait()  # Context exit stops the runtime on cancellation.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-directory", type=Path, required=True)
    parser.add_argument("--request-id", type=UUID)
    parser.add_argument("--platform-url")
    parser.add_argument("--name")
    parser.add_argument("--capability", action="append")
    args = parser.parse_args()
    if not args.state_directory.is_absolute():
        parser.error("--state-directory must be absolute")
    if args.request_id is None and not all((args.platform_url, args.name, args.capability)):
        parser.error("New requests require --platform-url, --name and --capability")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
