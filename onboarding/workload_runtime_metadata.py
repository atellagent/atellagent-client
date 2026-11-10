# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Trusted deployment metadata through exact activated workload mTLS/proof."""

from .http_response import request_json
from .runtime_metadata import RuntimeMetadata
from .service_account_http import create_service_account_http_client
from .setup_proofs import sign_setup_request
from .workload_activation_receipt import require_workload_activation_receipt
from .renewed_credential import load_renewed_credential


async def fetch_workload_runtime_metadata(*, draft, receipt, trust, draft_store, credential_store,
                                         device_proof_provider=None):
    saved = draft_store.require_saved(draft)
    stored = credential_store.load(draft=saved, receipt=receipt, trust=trust, draft_store=draft_store)
    require_workload_activation_receipt(draft=saved, receipt=receipt, stored=stored)
    stored = load_renewed_credential(stored, trust=trust, tenant_id=saved.setup.tenant_id)
    return await fetch_workload_metadata_for_credential(draft=saved, trust=trust, stored=stored,
        device_proof_provider=device_proof_provider)


async def fetch_workload_metadata_for_credential(*, draft, trust, stored, device_proof_provider=None):
    """Current machine gate also permits verification of a retained candidate."""
    verified = trust.validated(expected_origin=draft.setup.platform_origin)
    path = f"/api/client-onboarding/requests/{draft.proposal.id}/runtime-metadata/{stored.credential.operation_id}"
    headers = sign_setup_request(private_key=draft.setup.keys.proof_key, operation_id=draft.setup.operation_id,
                                method="GET", path=path, body=None)
    if (draft.proposal.device_id is None) != (device_proof_provider is None):
        from .errors import OnboardingClientError
        raise OnboardingClientError("onboarding_mcp_device_required")
    if device_proof_provider is not None:
        headers.update(device_proof_provider("GET", verified.service_account_api_origin + path, None))
    async with create_service_account_http_client(tenant_id=draft.setup.tenant_id,
        service_account_id=stored.credential.service_account_id, certificate_path=stored.certificate_path,
        private_key_path=stored.private_key_path, certificate_sha256=stored.credential.certificate_sha256) as client:
        payload = await request_json(client, method="GET", url=verified.service_account_api_origin + path,
                                     headers=headers, error_prefix="onboarding")
    return RuntimeMetadata.from_payload(payload, expected_origin=draft.setup.platform_origin,
                                        expected_gateway_origin=verified.service_account_api_origin)
