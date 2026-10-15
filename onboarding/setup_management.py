# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Explicit account/tenant review and signed setup submission with human OAuth."""

import httpx

from .bootstrap_origin import bootstrap_origin
from .device_login_models import ManagementTokens
from .errors import OnboardingClientError
from .http_response import request_json
from .proofs import canonical_json
from .setup_identity import SetupIdentity
from .setup_draft import SetupDraft
from .setup_store import SetupStore
from .setup_proofs import sign_setup_request
from .setup_receipt import SetupReceipt


class SetupManagementClient:
    def __init__(self, *, origin: str, tokens: ManagementTokens):
        self.origin = bootstrap_origin(origin)
        # Validate even if a caller constructed the tokens model directly.
        verified = ManagementTokens.from_payload({"access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token, "expires_in": tokens.expires_in, "token_type": "Bearer"})
        self._authorization = "Bearer " + verified.access_token
        self._closed = False
        self._http = httpx.AsyncClient(http2=True, trust_env=False,
            follow_redirects=False, timeout=15)

    async def identity(self) -> SetupIdentity:
        if self._closed:
            raise OnboardingClientError("onboarding_management_closed")
        payload = await request_json(self._http, method="GET",
            url=self.origin + "/api/client-onboarding/identity",
            headers={"Authorization": self._authorization}, error_prefix="onboarding")
        return SetupIdentity.from_payload(payload)

    async def submit(self, *, draft: SetupDraft, store: SetupStore) -> SetupReceipt:
        saved = store.require_saved(draft, expected_origin=self.origin)
        # Recheck selection before signing; the server independently enforces
        # current canonical owner/membership/RBAC/agreement/write authority.
        identity = await self.identity()
        identity.require_selection(expected_user_id=saved.owner_user_id, tenant_id=saved.tenant_id)
        proposal = saved.proposal()
        path = "/api/client-onboarding/sessions"
        proof_headers = sign_setup_request(private_key=saved.keys.proof_key, operation_id=saved.operation_id,
            method="POST", path=path, body={"tenant_id": str(saved.tenant_id),
                "user_id": str(identity.user_id), "proposal": proposal})
        proof = {name: proof_headers[f"X-Atellagent-Onboarding-{name.title()}"]
            for name in ("timestamp", "nonce", "signature")}
        payload = await request_json(self._http, method="POST", url=self.origin + path,
            headers={"Authorization": self._authorization, "Content-Type": "application/json"},
            content=canonical_json({"tenant_id": str(saved.tenant_id), "proposal": proposal, "proof": proof}),
            error_prefix="onboarding")
        return SetupReceipt.from_payload(payload, operation_id=saved.operation_id, kind=saved.kind)

    async def close(self):
        self._closed = True
        self._authorization = ""
        await self._http.aclose()

    async def submit_native(self, *, draft, store, installation):
        from .native_management import submit_native_request
        return await submit_native_request(self, draft=draft, store=store, installation=installation)

    async def submit_workload(self, *, draft, store, device_context=None):
        from .workload_management import submit_workload_request
        return await submit_workload_request(self, draft=draft, store=store, device_context=device_context)

    async def consent_native(self, *, draft, store, assignment, reviewed_intent_sha256):
        from .native_owner_consent import consent_native_assignment
        return await consent_native_assignment(self, draft=draft, store=store,
            assignment=assignment, reviewed_intent_sha256=reviewed_intent_sha256)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
