# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Fetch metadata without authentication from the caller-trusted application host."""

import httpx

from .bootstrap_metadata import BootstrapMetadata
from .bootstrap_origin import bootstrap_origin
from .http_response import request_json


async def fetch_bootstrap_metadata(origin: str) -> BootstrapMetadata:
    trusted = bootstrap_origin(origin)
    async with httpx.AsyncClient(http2=True, follow_redirects=False,
            trust_env=False, timeout=15) as client:
        payload = await request_json(client, method="GET",
            url=trusted + "/.well-known/atellagent-client", headers={}, error_prefix="onboarding_bootstrap")
    return BootstrapMetadata.from_payload(payload, expected_origin=trusted)
