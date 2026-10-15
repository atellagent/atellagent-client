# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bounded device-only requests: fresh proof, no browser token or automatic login."""

import httpx
import asyncio

from .http_response import request_json
from .proofs import canonical_json, sign_device_request
from .origins import https_origin
from .errors import OnboardingClientError


async def device_request(client: httpx.AsyncClient, *, origin: str, device_id, proof_key,
    method: str, path: str, body=None) -> dict:
    url = https_origin(origin) + path
    content = canonical_json(body) if body is not None else None
    for attempt in range(2):
        headers = sign_device_request(private_key=proof_key, device_id=device_id, method=method, url=url, body=body)
        if content is not None:
            headers["Content-Type"] = "application/json"
        try:
            return await request_json(client, method=method, url=url, headers=headers,
                content=content, error_prefix="device")
        except OnboardingClientError as exc:
            # Retry one rejected read, never an ambiguous write, timeout or
            # long security block. Each attempt has a fresh request proof.
            if (attempt != 0 or method != "GET" or exc.status != 429
                    or exc.retry_after is None or not 0 <= exc.retry_after <= 5):
                raise
            await asyncio.sleep(max(1, exc.retry_after))
