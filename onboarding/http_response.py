# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bounded JSON transport with safe errors and no redirect credential forwarding."""

import asyncio
import json

import httpx

from .errors import OnboardingClientError


async def request_json(client: httpx.AsyncClient, *, method: str, url: str,
    headers: dict[str, str], content: bytes | None = None, error_prefix: str) -> dict:
    try:
        async with asyncio.timeout(20), client.stream(method, url, headers=headers,
                content=content, follow_redirects=False) as response:
            encoded = bytearray()
            async for chunk in response.aiter_bytes():
                if len(encoded) + len(chunk) > 65536:
                    raise OnboardingClientError(f"{error_prefix}_response_invalid")
                encoded.extend(chunk)
            if 300 <= response.status_code < 400:
                raise OnboardingClientError(f"{error_prefix}_redirect_rejected", status=response.status_code)
            try:
                payload = json.loads(encoded)
            except (ValueError, UnicodeError) as exc:
                if response.status_code == 200:
                    raise OnboardingClientError(f"{error_prefix}_response_invalid", status=response.status_code) from exc
                payload = {}
            if response.status_code != 200:
                error = payload.get("error") if isinstance(payload, dict) else None
                code = error.get("code") if isinstance(error, dict) else None
                raise OnboardingClientError(code if isinstance(code, str) else f"{error_prefix}_request_rejected",
                    retryable=response.status_code in (408, 429, 500, 502, 503, 504), status=response.status_code,
                    retry_after=_retry_after(response))
            if not isinstance(payload, dict):
                raise OnboardingClientError(f"{error_prefix}_response_invalid")
            return payload
    except (httpx.HTTPError, TimeoutError) as exc:
        raise OnboardingClientError(f"{error_prefix}_transport_unavailable", retryable=True) from exc


def _retry_after(response):
    value = response.headers.get("Retry-After", "")
    return min(int(value), 86400) if value.isascii() and value.isdigit() and len(value) <= 6 else None
