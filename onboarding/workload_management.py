# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Attended workload submission; the operator is not its ongoing acting user."""

from .errors import OnboardingClientError
from .http_response import request_json
from .proofs import canonical_json
from .setup_proofs import sign_setup_request
from .workload_request_receipt import WorkloadRequestReceipt


async def submit_workload_request(client, *, draft, store, device_context=None, device_request=None):
    saved = store.require_saved(draft)
    setup = saved.setup
    if setup.platform_origin != client.origin:
        raise OnboardingClientError("onboarding_workload_setup_intent_changed")
    identity = await client.identity()
    identity.require_selection(expected_user_id=setup.owner_user_id, tenant_id=setup.tenant_id)
    proposal = saved.proposal.payload()
    path = f"/api/client-onboarding/sessions/{setup.operation_id}/workload"
    body = {"tenant_id": str(setup.tenant_id),
            "setup_operator_id": str(setup.owner_user_id), "proposal": proposal}
    headers = sign_setup_request(private_key=setup.keys.proof_key, operation_id=setup.operation_id,
        method="POST", path=path, body=body)
    proof = {name: headers[f"X-Atellagent-Onboarding-{name.title()}"]
             for name in ("timestamp", "nonce", "signature")}
    device_proof = None
    if device_context is not None and device_request is not None:
        raise OnboardingClientError("onboarding_workload_setup_intent_changed")
    if saved.proposal.device_id is not None:
        if device_request is not None:
            from .setup_request_batch_identity import require_proposal_device
            device_setup = require_proposal_device(device_request, saved)
        else:
            from .workload_device_context import require_device_association
            device_setup = require_device_association(device_context, saved)
        signed = sign_setup_request(private_key=device_setup.keys.proof_key,
            operation_id=device_setup.operation_id, method="POST", path=path, body=body)
        device_proof = {name: signed[f"X-Atellagent-Onboarding-{name.title()}"]
                        for name in ("timestamp", "nonce", "signature")}
    elif device_context is not None or device_request is not None:
        raise OnboardingClientError("onboarding_workload_setup_intent_changed")
    result = await request_json(client._http, method="POST", url=client.origin + path,
        headers={"Authorization": client._authorization, "Content-Type": "application/json"},
        content=canonical_json({"tenant_id": str(setup.tenant_id), "proposal": proposal,
                               "proof": proof, "device_proof": device_proof}),
        error_prefix="onboarding")
    return WorkloadRequestReceipt.from_payload(result, draft=saved)
