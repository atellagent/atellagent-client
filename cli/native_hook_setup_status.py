"""Combine current settings with accepted delivery evidence for setup guidance."""

from atellagent_client.integrations.agents.hook_verification import load
from .setup_native_hooks import _review


def status(directory, draft, endpoint, delegation):
    review, _ = _review(directory, draft.proposal.id, hooks_only=True)
    if any(item["changed"] for item in review.public["files"]):
        return "settings_required"
    receipt = load(endpoint)
    if receipt is None or delegation is None:
        return "waiting_for_tool_result"
    # The review checks the current managed hook definitions and permissions.
    # Whole-file timestamps also change for unrelated host preferences/state;
    # they cannot invalidate an accepted result for this exact registration.
    # No raw tool input or result is retained.
    if (receipt["request_id"] == str(draft.proposal.id)
            and receipt["authorization_id"] == delegation["authorization_id"]
            and receipt["authorization_epoch"] == delegation["authorization_epoch"]):
        return "verified"
    return "waiting_for_tool_result"
