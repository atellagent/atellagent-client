# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Exact persisted workload SA mTLS and original setup proof activate its leaf."""

from .http_response import request_json
from .proofs import canonical_json
from .service_account_http import create_service_account_http_client
from .setup_proofs import sign_setup_request
from .workload_activation_receipt import WorkloadActivationResult, save_workload_activation_receipt


async def activate_stored_workload(*, draft, receipt, trust, draft_store, credential_store,
                                   device_proof_provider=None):
    saved = draft_store.require_saved(draft)
    stored = credential_store.load(draft=saved, receipt=receipt, trust=trust, draft_store=draft_store)
    # load verifies independent signing trust, retained key, exact intent and the
    # immutable initial selection before this dedicated mTLS pool is created.
    verified_trust = trust.validated(expected_origin=saved.setup.platform_origin)
    body = {"operation_id": str(stored.credential.operation_id),
            "certificate_sha256": stored.credential.certificate_sha256}
    path = f"/api/client-onboarding/requests/{saved.proposal.id}/activate"
    headers = sign_setup_request(private_key=saved.setup.keys.proof_key, operation_id=saved.setup.operation_id,
                                method="POST", path=path, body=body)
    headers["Content-Type"] = "application/json"
    if (saved.proposal.device_id is not None) != (device_proof_provider is not None):
        from .errors import OnboardingClientError
        raise OnboardingClientError("onboarding_mcp_device_required")
    if device_proof_provider is not None:
        headers.update(device_proof_provider("POST", verified_trust.service_account_api_origin + path, body))
    async with create_service_account_http_client(tenant_id=saved.setup.tenant_id,
        service_account_id=stored.credential.service_account_id, certificate_path=stored.certificate_path,
        private_key_path=stored.private_key_path, certificate_sha256=stored.credential.certificate_sha256) as client:
        payload = await request_json(client, method="POST", url=verified_trust.service_account_api_origin + path,
            headers=headers, content=canonical_json(body), error_prefix="onboarding")
    result = WorkloadActivationResult.from_payload(payload, stored=stored)
    save_workload_activation_receipt(draft=saved, receipt=receipt, stored=stored)
    return result
