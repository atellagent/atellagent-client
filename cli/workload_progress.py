"""Terminal presentation of shared workload approval/activation progress."""

import os
import shlex
import subprocess

from atellagent_client.sdk import setup_progress


async def wait_for_workload(flow, seconds, *, state_directory):
    labels = {"awaiting_approval": "Awaiting administrator approval in Discovery.",
        "issuing": "Preparing workload credentials.",
        "awaiting_registration": "Credentials activated. Start the workload to register it.",
        "registered": "Workload registered; action authorization remains checked per request."}
    previous = None
    async for progress in setup_progress(flow, seconds=seconds):
        if progress.stage == "retained":
            command = ["atellagent-cli", "workload", "resume", str(flow.draft.proposal.id),
                       "--state-directory", str(state_directory)]
            rendered = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
            print(f"Setup remains saved. Continue with: {rendered}", flush=True)
            return None
        label = (labels[progress.stage] if progress.stage != "temporarily_unavailable" else
                 f"Connection temporarily unavailable ({progress.error_code}); retrying.")
        if label != previous:
            print(label, flush=True)
            previous = label
        if progress.stage in {"awaiting_registration", "registered"}:
            return progress.step
