"""Terminal/desktop presentation of shared native setup progress."""

from atellagent_client.sdk import setup_progress


async def wait_for_native(flow, seconds, *, interaction=None):
    request_id = flow.draft.proposal.id
    labels = {"awaiting_approval": "Awaiting administrator approval in Discovery.",
        "awaiting_owner_consent": f"Owner review required: atellagent-cli setup native consent {request_id}",
        "issuing": "Preparing integration credentials.",
        "awaiting_registration": "Credentials activated. Host registration and hook setup are not complete.",
        "registered": "Integration registered. Hook readiness must be verified separately."}
    previous = None
    async for progress in setup_progress(flow, seconds=seconds):
        stage = progress.stage
        if stage == "retained":
            if interaction is None:
                print(f"Setup is saved. Continue with: atellagent-cli setup native resume {request_id}", flush=True)
            else:
                interaction.emit("native_wait_ended", request_id=str(request_id))
            return None
        label = (labels[stage] if stage != "temporarily_unavailable" else
                 f"Connection temporarily unavailable ({progress.error_code}); retrying.")
        if label != previous:
            if interaction is None:
                print(label, flush=True)
            else:
                interaction.emit("native_progress", request_id=str(request_id), stage=stage)
            previous = label
        if stage in {"awaiting_owner_consent", "awaiting_registration", "registered"}:
            return progress.step
