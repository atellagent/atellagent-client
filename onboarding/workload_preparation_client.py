"""Retained workload bootstrap proof retrieves and picks up reviewed settings."""

from uuid import UUID
from dataclasses import replace

import httpx

from .errors import OnboardingClientError
from .http_response import request_json
from .proofs import canonical_json
from .setup_proofs import sign_setup_request
from .workload_preparation import WorkloadPreparation
from .workload_preparation_page import WorkloadPreparationPage
from .workload_request_receipt import WorkloadRequestReceipt
from .workload_setup_draft import WorkloadSetupDraft


class WorkloadPreparationClient:
    def __init__(self, *, setup, setup_store):
        self.setup = setup_store.require_saved(setup, expected_origin=setup.platform_origin)
        if self.setup.kind != "workload":
            raise ValueError("Retained workload bootstrap required")
        self._http = httpx.AsyncClient(http2=True, trust_env=False, follow_redirects=False, timeout=15)

    def headers(self, method, path, body=None):
        return sign_setup_request(private_key=self.setup.keys.proof_key, operation_id=self.setup.operation_id,
            method=method, path=path, body=body)

    async def preparations(self, *, after=None):
        if after is not None and not isinstance(after, UUID):
            raise ValueError("Typed workload cursor required")
        path = f"/api/client-onboarding/sessions/{self.setup.operation_id}/preparations"
        if after is not None: path += f"?after={after}"
        payload = await request_json(self._http, method="GET", url=self.setup.platform_origin + path,
            headers=self.headers("GET", path), error_prefix="onboarding")
        return WorkloadPreparationPage.from_payload(payload, setup=self.setup, after=after)

    async def pickup(self, preparation, *, store, device_context=None, local_inputs_store=None, inputs=None):
        if not isinstance(preparation, WorkloadPreparation) or preparation.id != preparation.proposal.id:
            raise ValueError("Exact typed workload preparation required")
        if preparation.status not in ("awaiting_proposal", "proposed"):
            raise OnboardingClientError("onboarding_workload_preparation_not_pickable")
        proposal = preparation.proposal
        if device_context is not None:
            proposal = replace(proposal, device_id=device_context.client.device.device_id,
                device_onboarding_session_id=device_context.setup.operation_id)
        draft = WorkloadSetupDraft(self.setup, proposal)
        device_setup = None
        if proposal.device_id is not None:
            from .workload_device_context import require_device_association
            device_setup = require_device_association(device_context, draft)
        try:
            store.save_initial(draft)
        except OnboardingClientError as exc:
            if exc.code != "onboarding_workload_setup_already_saved": raise
        saved = store.require_saved(draft)
        if inputs is not None:
            if local_inputs_store is None:
                raise ValueError("Local input storage required")
            local_inputs_store.save(draft=saved, inputs=inputs)
        path = f"/api/client-onboarding/sessions/{self.setup.operation_id}/preparations/{preparation.id}/propose"
        body = {"tenant_id": str(self.setup.tenant_id), "proposal": saved.proposal.payload()}
        headers = self.headers("POST", path, body)
        proof = {name: headers[f"X-Atellagent-Onboarding-{name.title()}"] for name in ("timestamp", "nonce", "signature")}
        device_proof = None
        if device_setup is not None:
            signed = sign_setup_request(private_key=device_setup.keys.proof_key,
                operation_id=device_setup.operation_id, method="POST", path=path, body=body)
            device_proof = {name: signed[f"X-Atellagent-Onboarding-{name.title()}"]
                            for name in ("timestamp", "nonce", "signature")}
        result = await request_json(self._http, method="POST", url=self.setup.platform_origin + path,
            headers={"Content-Type": "application/json"},
            content=canonical_json({**body, "proof": proof, "device_proof": device_proof}), error_prefix="onboarding")
        return saved, WorkloadRequestReceipt.from_payload(result, draft=saved)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._http.aclose()
