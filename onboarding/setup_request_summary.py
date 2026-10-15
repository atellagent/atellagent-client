"""Local submission facts for the pending device's selected connections."""

from .setup_request_batch_store import SetupRequestBatchStore
from .errors import OnboardingClientError


def pending_connection_summary(directory, setup, receipt):
    store = SetupRequestBatchStore(directory)
    try:
        store.path.lstat()
    except FileNotFoundError:
        return [], False
    if receipt is None:
        raise OnboardingClientError("onboarding_device_request_required")
    _, choices = store.load(setup, receipt)
    names = [choice["name"] for choice in (*choices.native, *choices.mcp)]
    # This is a retained submission receipt, not an administrator decision.
    return names, not store.needs_submission()
