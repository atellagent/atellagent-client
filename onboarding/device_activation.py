# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Only a verified mTLS activation receipt promotes a stored candidate device."""

from dataclasses import dataclass

from .device_csr import device_certificate_operation_id
from .device_activation_receipt import save_activation_receipt
from .device_http import create_device_http_client
from .device_identity import AdmittedDevice
from .errors import OnboardingClientError
from .http_response import request_json
from .proofs import canonical_json
from .setup_proofs import sign_setup_request


@dataclass(frozen=True)
class ActivatedDevice:
    device: AdmittedDevice
    reporting_enabled: bool


async def activate_stored_device(*, draft, receipt, trust, setup_store, credential_store,
    fingerprint: str, reporting_enabled: bool) -> ActivatedDevice:
    if type(reporting_enabled) is not bool:
        raise ValueError("explicit reporting preference required")
    saved = setup_store.require_saved(draft, expected_origin=trust.platform_origin)
    stored = credential_store.load(draft=saved, receipt=receipt, trust=trust, fingerprint=fingerprint)
    body = {"operation_id": str(device_certificate_operation_id(saved.operation_id)),
        "certificate_sha256": stored.credential.certificate_sha256,
        "reporting_enabled": reporting_enabled}
    path = f"/api/client-onboarding/sessions/{saved.operation_id}/activate"
    headers = sign_setup_request(private_key=saved.keys.proof_key,
        operation_id=saved.operation_id, method="POST", path=path, body=body)
    headers["Content-Type"] = "application/json"
    # Server TLS verification uses ordinary system roots, not the device issuer chain.
    async with create_device_http_client(tenant_id=saved.tenant_id,
        device_id=stored.credential.device_id, certificate_path=stored.certificate_path,
        private_key_path=stored.private_key_path) as client:
        result = await request_json(client, method="POST", url=trust.device_api_origin + path,
            headers=headers, content=canonical_json(body), error_prefix="onboarding")
    if (set(result) != {"device_id", "status", "reporting_enabled"}
        or result["device_id"] != str(stored.credential.device_id) or result["status"] != "device_ready"
        or type(result["reporting_enabled"]) is not bool):
        raise OnboardingClientError("onboarding_activation_response_invalid")
    # Receipt recovery may return a later opt-out; never force the original preference.
    save_activation_receipt(saved, stored)
    return ActivatedDevice(AdmittedDevice(saved.tenant_id, stored.credential.device_id,
        saved.owner_user_id, saved.keys.proof_key, stored.certificate_path, stored.private_key_path),
        result["reporting_enabled"])
