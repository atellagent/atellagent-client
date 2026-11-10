"""Attended login with organization choice only when membership is ambiguous."""

import asyncio

from atellagent_client.sdk import DeviceLoginClient, OnboardingClientError


async def login(origin, *, interaction=None):
    async with await DeviceLoginClient.open(origin) as client:
        challenge = await client.begin()
        if interaction is None:
            print(f"Sign in at {challenge.browser_url}\nCode: {challenge.user_code}", flush=True)
        else:
            interaction.sign_in(challenge)
        return await client.wait()


async def select_membership(management, *, interaction=None):
    identity = await management.identity()
    if not identity.memberships:
        raise OnboardingClientError("onboarding_membership_required")
    if len(identity.memberships) == 1:
        tenant = identity.memberships[0].tenant_id
        identity.require_selection(expected_user_id=identity.user_id, tenant_id=tenant)
        if interaction is None:
            print(f"Organization: {identity.memberships[0].tenant_name}", flush=True)
        return identity.user_id, tenant
    if interaction is not None:
        return await interaction.choose_membership(identity)
    print(f"Signed-in account: {identity.user_id}")
    for index, membership in enumerate(identity.memberships, 1):
        print(f"{index}. {membership.tenant_name}")
    choice = await asyncio.to_thread(input, "Choose organization number: ")
    if not choice.isascii() or not choice.isdigit() or not 1 <= int(choice) <= len(identity.memberships):
        raise OnboardingClientError("onboarding_organization_selection_invalid")
    tenant = identity.memberships[int(choice) - 1].tenant_id
    identity.require_selection(expected_user_id=identity.user_id, tenant_id=tenant)
    return identity.user_id, tenant
