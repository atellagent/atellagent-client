# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bounded device-code polling; cancellation never starts a replacement login."""

import asyncio
from time import monotonic

from .device_login_models import ManagementTokens
from .errors import OnboardingClientError
from .oauth_http import oauth_form


async def poll_login(client, metadata, challenge) -> ManagementTokens:
    interval = challenge.interval
    remaining = challenge.deadline - monotonic()
    if remaining <= 0:
        raise OnboardingClientError("onboarding_login_expired")
    try:
        async with asyncio.timeout(remaining):
            while True:
                await asyncio.sleep(interval)
                status, payload = await oauth_form(client, metadata.token_url, {
                    "client_id": metadata.client_id,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": challenge.device_code, "code_verifier": challenge.code_verifier})
                if status == 200:
                    return ManagementTokens.from_payload(payload)
                error = payload.get("error")
                if status == 400 and error == "authorization_pending":
                    continue
                if status == 400 and error == "slow_down":
                    interval += 5
                    continue
                if status == 400 and error == "access_denied":
                    raise OnboardingClientError("onboarding_login_denied")
                if status == 400 and error == "expired_token":
                    raise OnboardingClientError("onboarding_login_expired")
                raise OnboardingClientError("onboarding_login_rejected", status=status)
    except TimeoutError as exc:
        raise OnboardingClientError("onboarding_login_expired") from exc
