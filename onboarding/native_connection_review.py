"""Exact public native consent projection, shared by all customer interfaces."""

from .device_assignments import DeviceAssignment, IDENTITIES
from .errors import OnboardingClientError


async def connection_review(context, request_id):
    draft = context.load(request_id)
    assignment = await context.client.assignment(request_id)
    if assignment is None:
        raise OnboardingClientError("onboarding_native_assignment_missing")
    assignment = DeviceAssignment.from_payload(assignment.payload(), device=draft)
    if (assignment.proposal != draft.proposal or assignment.onboarding_session_id != draft.setup_id
            or assignment.status not in ("pending", "approved")):
        raise OnboardingClientError("onboarding_native_consent_intent_changed")
    proposal = assignment.proposal
    review = {field: str(getattr(assignment, field)) for field in IDENTITIES}
    review.update(tenant_id=str(draft.tenant_id), owner_user_id=str(draft.owner_user_id),
        device_id=str(draft.device_id), display_name=proposal.display_name,
        native_host=proposal.native_host, packaging=proposal.packaging,
        enforcement_mode=proposal.enforcement_mode, coverage_posture=proposal.coverage_posture,
        capabilities=list(assignment.capabilities), delegation_lifetime=assignment.delegation_lifetime,
        intent_sha256=assignment.intent_sha256)
    return assignment, review
