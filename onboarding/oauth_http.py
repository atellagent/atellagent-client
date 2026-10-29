# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bounded OAuth form exchange; never expose response bodies in errors."""

import asyncio
import json

import httpx

from .errors import OnboardingClientError


async def oauth_form(client: httpx.AsyncClient, url: str, data: dict) -> tuple[int, dict]:
    try:
        async with asyncio.timeout(20), client.stream("POST", url, data=data,
                follow_redirects=False) as response:
            encoded = bytearray()
            async for chunk in response.aiter_bytes():
                if len(encoded) + len(chunk) > 65536:
                    raise OnboardingClientError("onboarding_oauth_response_invalid")
                encoded.extend(chunk)
            if 300 <= response.status_code < 400:
                raise OnboardingClientError("onboarding_oauth_redirect_rejected", status=response.status_code)
            if response.status_code in (408, 429, 500, 502, 503, 504):
                raise OnboardingClientError("onboarding_oauth_unavailable", retryable=True,
                    status=response.status_code)
            try:
                payload = json.loads(encoded)
            except (ValueError, UnicodeError) as exc:
                raise OnboardingClientError("onboarding_oauth_response_invalid") from exc
            if not isinstance(payload, dict):
                raise OnboardingClientError("onboarding_oauth_response_invalid")
            return response.status_code, payload
    except (httpx.HTTPError, TimeoutError) as exc:
        raise OnboardingClientError("onboarding_oauth_unavailable", retryable=True) from exc
