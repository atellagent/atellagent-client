"""Visible exact owner review before attended consent, never a hidden prompt."""

import asyncio

from atellagent_client.sdk import SetupManagementClient
from .setup_login import login
from .setup_native_review import show_review


async def consent(context, draft, *, interaction=None):
    assignment, review = await context.review(draft.proposal.id)
    if interaction is None:
        show_review(review)
        approved = await asyncio.to_thread(input, "Consent to this exact connection? Type yes: ") == "yes"
    else:
        approved = await interaction.confirm_native(review)
    if not approved:
        if interaction is None:
            print("Consent not submitted. Existing request is unchanged.", flush=True)
        else:
            interaction.emit("consent_not_submitted", request_id=str(assignment.request_id))
        return False
    tokens = (await login(draft.platform_origin) if interaction is None
        else await login(draft.platform_origin, interaction=interaction))
    async with SetupManagementClient(origin=draft.platform_origin, tokens=tokens) as management:
        await context.consent(management, draft.proposal.id,
                              reviewed_intent_sha256=assignment.intent_sha256)
    if interaction is None:
        print("Owner consent recorded. Administrator approval is separate.", flush=True)
    else:
        interaction.emit("owner_consent_recorded", request_id=str(assignment.request_id))
    return True
