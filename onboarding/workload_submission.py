# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Attended submission recovers immutable bootstrap storage without new keys."""

from .errors import OnboardingClientError


async def submit_saved_workload(management, *, context, draft):
    saved = context.drafts.require_saved(draft)
    try:
        context.setups.save_initial(saved.setup)
    except OnboardingClientError as exc:
        if exc.code != "onboarding_setup_already_saved": raise
    context.setups.require_saved(saved.setup, expected_origin=saved.setup.platform_origin)
    await management.submit(draft=saved.setup, store=context.setups)
    receipt = await management.submit_workload(draft=saved, store=context.drafts,
        device_context=context.device_context)
    return context.receipts.bind(draft=saved, receipt=receipt)
