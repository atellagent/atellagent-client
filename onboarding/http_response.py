# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bounded JSON transport with safe errors and no redirect credential forwarding."""

import asyncio
import json
import ssl

import httpx

from .errors import OnboardingClientError


def _transient_connect_error(error):
    # Invalid TLS material is not a transient connection failure. Walk a bounded
    # cause tree because backend exceptions can wrap an SSL failure in a group.
    pending, seen = [error], set()
    for _ in range(16):
        if not pending:
            return True
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return False
        pending.extend(getattr(current, "exceptions", ()))
        for related in (current.__cause__, current.__context__):
            if related is not None:
                pending.append(related)
    return False


async def request_json(client: httpx.AsyncClient, *, method: str, url: str,
    headers: dict[str, str], content: bytes | None = None, error_prefix: str) -> dict:
    # Connection establishment sends no HTTP request. Permit one replacement
    # connection for a mutation, never a read/write/protocol failure or a response
    # rejection. Body-free reads retain their existing three-attempt budget.
    # All attempts share the original deadline and exact signed target/proof;
    # TLS verification, credentials, headers and redirect policy are unchanged.
    attempts = 3 if method == "GET" and not content else 2
    configured = client.timeout
    timeout = httpx.Timeout(connect=(2.0 if configured.connect is None
                                     else min(2.0, configured.connect)),
                            read=configured.read, write=configured.write, pool=configured.pool)
    try:
        async with asyncio.timeout(20):
            for attempt in range(attempts):
                response_started = False
                try:
                    async with client.stream(method, url, headers=headers,
                            content=content, follow_redirects=False, timeout=timeout) as response:
                        response_started = True
                        return await _read_response(response, error_prefix)
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    if (response_started or attempt + 1 == attempts
                            or not _transient_connect_error(exc)):
                        raise
                    await asyncio.sleep(0.25 * (2 ** attempt))
    except (httpx.HTTPError, TimeoutError) as exc:
        raise OnboardingClientError(f"{error_prefix}_transport_unavailable", retryable=True) from exc


async def _read_response(response, error_prefix):
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


def _retry_after(response):
    value = response.headers.get("Retry-After", "")
    return min(int(value), 86400) if value.isascii() and value.isdigit() and len(value) <= 6 else None
