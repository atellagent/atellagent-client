"""CLI/desktop rendering of shared MCP approval and activation progress."""

from atellagent_client.sdk import setup_progress


async def wait_for_mcp(flow, seconds, *, interaction=None):
    request_id = str(flow.draft.proposal.id)
    previous = None
    async for progress in setup_progress(flow, seconds=seconds):
        if progress.stage == "retained":
            if interaction is None:
                print(f"Request saved. Continue: atellagent-cli setup mcp resume {request_id}", flush=True)
            else:
                interaction.emit("mcp_wait_ended", request_id=request_id)
            return
        if progress.stage != previous:
            if interaction is None:
                labels = {"awaiting_approval": "Awaiting administrator approval in Discovery.",
                    "issuing": "Preparing MCP integration credentials.",
                    "awaiting_registration": "Credentials activated. Review and start this MCP bridge to register it.",
                    "registered": "MCP integration registered. Local process status is separate.",
                    "temporarily_unavailable": "Connection temporarily unavailable; retrying."}
                print(labels[progress.stage], flush=True)
            else:
                interaction.emit("mcp_progress", request_id=request_id, stage=progress.stage)
            previous = progress.stage
