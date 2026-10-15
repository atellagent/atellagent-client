# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Signed setup status/cancellation over server-authenticated HTTPS."""

from uuid import UUID

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .errors import OnboardingClientError
from .http_response import request_json
from .origins import https_origin
from .setup_proofs import sign_setup_request
from .setup_progress import SetupProgress


class SetupStatusClient:
    def __init__(self, *, origin: str, operation_id: UUID, proof_key: Ed25519PrivateKey):
        self.origin = https_origin(origin)
        if not isinstance(operation_id, UUID) or not isinstance(proof_key, Ed25519PrivateKey):
            raise ValueError("setup identity and proof key required")
        self.operation_id, self.proof_key = operation_id, proof_key
        self._http = httpx.AsyncClient(http2=True, trust_env=False,
            follow_redirects=False, timeout=15)

    async def status(self) -> SetupProgress:
        return SetupProgress.from_payload(await self._request("GET"), operation_id=self.operation_id)

    async def cancel(self) -> SetupProgress:
        return SetupProgress.from_payload(await self._request("DELETE"), operation_id=self.operation_id)

    async def _request(self, method: str) -> dict:
        path = f"/api/client-onboarding/sessions/{self.operation_id}"
        headers = sign_setup_request(private_key=self.proof_key,
            operation_id=self.operation_id, method=method, path=path)
        result = await request_json(self._http, method=method, url=self.origin + path,
            headers=headers, error_prefix="onboarding")
        if result.get("id") != str(self.operation_id):
            raise OnboardingClientError("onboarding_response_invalid")
        return result

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
