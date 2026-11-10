"""Foreground command lifecycle and presentation for connected workloads."""

from .shutdown import wait_for_shutdown_signal


async def run_participant(context, request_id, *, verify=False):
    async with context.open_runtime(request_id) as participant:
        print(f"Connected participant registered: {participant.instance_id}", flush=True)
        if not verify:
            await wait_for_shutdown_signal()
