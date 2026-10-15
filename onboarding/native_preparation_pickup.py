"""Device-only prepared assignment pickup, with no browser login or activation."""

from uuid import UUID

from .errors import OnboardingClientError
from .native_preparation_draft import preparation_draft
from .native_preparation_receipt import pickup_intent_digest


async def pickup_preparation(context, assignment_id):
    if not isinstance(assignment_id, UUID):
        raise ValueError("Exact typed preparation identity required")
    page = await context.client.preparations(assignment_id=assignment_id)
    if len(page.preparations) != 1 or page.preparations[0].assignment_id != assignment_id:
        raise OnboardingClientError("onboarding_native_preparation_missing")
    draft = preparation_draft(context, page.preparations[0])
    result = await context.client._request("POST", f"/devices/self/preparations/{assignment_id}/propose",
        {"proposal": draft.proposal.payload()})
    digest = pickup_intent_digest(result, request_id=assignment_id)
    assignment = await context.client.assignment(assignment_id)
    if (assignment is None or assignment.intent_sha256 != digest
            or assignment.proposal != draft.proposal or assignment.onboarding_session_id != draft.setup_id):
        raise OnboardingClientError("onboarding_native_setup_intent_changed")
    return assignment
