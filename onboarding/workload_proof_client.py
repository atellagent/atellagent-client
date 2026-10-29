# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Workload polling/CSR staging needs retained keys, never a human session."""

import base64
from hashlib import sha256
import httpx

from .contract_values import aware_time
from .errors import OnboardingClientError
from .http_response import request_json
from .proofs import canonical_json
from .service_account_csr import service_account_csr_facts
from .setup_proofs import sign_setup_request
from .workload_request_receipt import WorkloadRequestReceipt


class WorkloadProofClient:
    def __init__(self, *, draft, store, receipt):
        self.draft = store.require_saved(draft)
        self.receipt = WorkloadRequestReceipt.from_payload(receipt.payload(), draft=self.draft)
        self.origin = self.draft.setup.platform_origin
        self._http = httpx.AsyncClient(http2=True, trust_env=False, follow_redirects=False, timeout=15)

    def headers(self, method, path, body=None):
        setup = self.draft.setup
        return sign_setup_request(private_key=setup.keys.proof_key, operation_id=setup.operation_id,
                                  method=method, path=path, body=body)

    async def status(self):
        path = f"/api/client-onboarding/requests/{self.receipt.id}/status"
        result = await request_json(self._http, method="GET", url=self.origin + path,
            headers=self.headers("GET", path), error_prefix="onboarding")
        current = WorkloadRequestReceipt.from_payload(result, draft=self.draft)
        if ((current.service_account_id, current.integration_id, current.intent_sha256) !=
                (self.receipt.service_account_id, self.receipt.integration_id, self.receipt.intent_sha256)):
            raise OnboardingClientError("onboarding_workload_receipt_invalid")
        return current

    def csr_facts(self, csr_pem):
        facts = service_account_csr_facts(csr_pem)
        retained_digest = sha256(base64.b64decode(
            self.draft.setup.proposal()["tls_public_key_der_base64"], validate=True)).hexdigest()
        if ((facts.tenant_id, facts.service_account_id, facts.public_key_sha256) !=
                (self.draft.setup.tenant_id, self.receipt.service_account_id, retained_digest)):
            raise OnboardingClientError("onboarding_workload_csr_invalid")
        return facts

    async def stage(self, *, csr_pem):
        facts = self.csr_facts(csr_pem)
        path = f"/api/client-onboarding/requests/{self.receipt.id}/claim"
        body = {"tenant_id": str(self.draft.setup.tenant_id), "request_id": str(self.receipt.id),
            "revision": 1, "intent_sha256": self.receipt.intent_sha256,
            "tls_public_key_sha256": facts.public_key_sha256, "csr_sha256": facts.csr_sha256}
        headers = self.headers("POST", path, body)
        proof = {name: headers[f"X-Atellagent-Onboarding-{name.title()}"]
                 for name in ("timestamp", "nonce", "signature")}
        payload = {"revision": 1, "intent_sha256": self.receipt.intent_sha256, "csr_pem": facts.pem, "proof": proof}
        result = await request_json(self._http, method="POST", url=self.origin + path,
            headers={"Content-Type": "application/json"}, content=canonical_json(payload), error_prefix="onboarding")
        try:
            if (set(result) != {"id", "status", "revision", "expires_at", "claim_expires_at"}
                    or result["id"] != str(self.receipt.id) or result["status"] != "issuing"
                    or type(result["revision"]) is not int or result["revision"] != 1):
                raise ValueError("exact staged workload required")
            aware_time(result["expires_at"])
            aware_time(result["claim_expires_at"])
        except (ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_response_invalid") from exc

    async def certificate(self, *, csr_pem, trust):
        from .workload_certificate_delivery import fetch_workload_certificate
        return await fetch_workload_certificate(self, csr_pem=csr_pem, trust=trust)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._http.aclose()
