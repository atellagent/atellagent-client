"""Terminal/desktop presentation of shared device setup progress."""

from atellagent_client.sdk import setup_progress

_LABELS = {"awaiting_approval": "Awaiting administrator approval in Discovery.",
    "issuing": "Preparing device credentials.", "device_ready": "Device enrolled. Discovery service can now start."}


async def wait_for_setup(flow, seconds: int, *, interaction=None) -> bool:
    previous = None
    async for progress in setup_progress(flow, seconds=seconds):
        stage = progress.stage
        if stage == "retained":
            if interaction is None:
                print("Setup is saved. Continue with: atellagent-cli setup resume", flush=True)
            else:
                interaction.emit("wait_ended", state="retained")
            return False
        label = (_LABELS[stage] if stage != "temporarily_unavailable" else
                 f"Connection temporarily unavailable ({progress.error_code}); retrying.")
        if stage == "device_ready" or label != previous:
            if interaction is None:
                print(label, flush=True)
            else:
                interaction.emit("setup_complete" if stage == "device_ready" else "progress", stage=stage)
            previous = label
        if stage == "device_ready":
            return True
    return False
