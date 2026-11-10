# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Native activation proves the integration leaf and original device setup key."""

from .errors import OnboardingClientError
from .http_response import request_json
from .native_activation_receipt import NativeActivationResult, save_native_activation_receipt
from .proofs import canonical_json
from .service_account_http import create_service_account_http_client
from .setup_proofs import sign_setup_request


async def activate_stored_native(*, draft, assignment, trust, draft_store, credential_store,
                                 fingerprint, installation):
    saved = draft_store.require_saved(draft)
    stored = credential_store.load_selected(draft=saved, assignment=assignment, trust=trust,
                                            draft_store=draft_store)
    if stored.credential.certificate_sha256 != fingerprint:
        raise OnboardingClientError("onboarding_native_credential_selection_changed")
    device_setup, receipt, _ = installation.load()
    if (device_setup.kind != "device" or receipt.kind != "device" or device_setup.operation_id != saved.setup_id
            or receipt.id != saved.setup_id or receipt.device_id != saved.device_id
            or device_setup.platform_origin != saved.platform_origin
            or (device_setup.tenant_id, device_setup.owner_user_id) != (saved.tenant_id, saved.owner_user_id)):
        raise OnboardingClientError("onboarding_native_setup_intent_changed")
    body = {"operation_id": str(stored.credential.operation_id), "certificate_sha256": fingerprint}
    path = f"/api/client-onboarding/requests/{saved.proposal.id}/native-activate"
    headers = sign_setup_request(private_key=device_setup.keys.proof_key, operation_id=saved.setup_id,
                                method="POST", path=path, body=body)
    headers["Content-Type"] = "application/json"
    async with create_service_account_http_client(tenant_id=saved.tenant_id,
        service_account_id=assignment.service_account_id, certificate_path=stored.certificate_path,
        private_key_path=stored.private_key_path, certificate_sha256=stored.credential.certificate_sha256) as client:
        payload = await request_json(client, method="POST", url=trust.service_account_api_origin + path,
            headers=headers, content=canonical_json(body), error_prefix="onboarding")
    result = NativeActivationResult.from_payload(payload, stored=stored)
    save_native_activation_receipt(draft=saved, assignment=assignment, stored=stored, result=result)
    return result
