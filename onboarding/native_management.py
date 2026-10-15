# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Owner-consented native proposals; administrator approval remains separate."""

from .errors import OnboardingClientError
from .http_response import request_json
from .native_request_receipt import NativeRequestReceipt
from .proofs import canonical_json
from .setup_proofs import sign_setup_request


async def submit_native_request(client, *, draft, store, installation):
    device_setup, device_receipt, _ = installation.load()
    return await submit_native_proposal(client, draft=draft, store=store,
        device_setup=device_setup, device_receipt=device_receipt)


async def submit_native_proposal(client, *, draft, store, device_setup, device_receipt):
    """Submit the owner's consent with the exact proposal, never activate it.

    Callers must disclose agent.control on this device until revoked before
    submission. Administrative assignment uses its separate prepared flow.
    """
    saved = store.require_saved(draft)
    if (saved.platform_origin != client.origin or device_setup.platform_origin != client.origin
            or device_setup.kind != "device" or device_receipt.kind != "device"
            or saved.setup_id != device_setup.operation_id or device_receipt.id != saved.setup_id
            or device_receipt.device_id != saved.device_id
            or (device_setup.tenant_id, device_setup.owner_user_id) != (saved.tenant_id, saved.owner_user_id)):
        raise OnboardingClientError("onboarding_native_setup_intent_changed")
    identity = await client.identity()
    identity.require_selection(expected_user_id=saved.owner_user_id, tenant_id=saved.tenant_id)
    proposal = saved.proposal.payload()
    path = f"/api/client-onboarding/sessions/{saved.setup_id}/connections"
    headers = sign_setup_request(private_key=device_setup.keys.proof_key, operation_id=saved.setup_id,
        method="POST", path=path, body={"tenant_id": str(saved.tenant_id),
            "owner_user_id": str(saved.owner_user_id), "proposal": proposal, "owner_consent": True})
    proof = {name: headers[f"X-Atellagent-Onboarding-{name.title()}"]
             for name in ("timestamp", "nonce", "signature")}
    result = await request_json(client._http, method="POST", url=client.origin + path,
        headers={"Authorization": client._authorization, "Content-Type": "application/json"},
        content=canonical_json({"tenant_id": str(saved.tenant_id), "proposal": proposal,
                               "proof": proof, "owner_consent": True}),
        error_prefix="onboarding")
    return NativeRequestReceipt.from_payload(result, draft=saved)
