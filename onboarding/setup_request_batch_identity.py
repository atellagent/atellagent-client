"""Proposal-only identity evidence, with no device/runtime transport methods."""

from .errors import OnboardingClientError


def require_proposal_device(device_request, workload):
    setup, receipt = device_request
    expected = (workload.setup.tenant_id, workload.setup.owner_user_id,
                workload.proposal.device_id, workload.proposal.device_onboarding_session_id)
    if (setup.kind != "device" or receipt.kind != "device" or receipt.id != setup.operation_id
            or setup.platform_origin != workload.setup.platform_origin
            or (setup.tenant_id, setup.owner_user_id, receipt.device_id, setup.operation_id) != expected):
        raise OnboardingClientError("onboarding_mcp_device_changed")
    return setup
