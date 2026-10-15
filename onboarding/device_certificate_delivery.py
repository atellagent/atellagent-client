# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Proof-bound public delivery; only independently verified leaves are returned."""

from dataclasses import dataclass
from uuid import UUID

import httpx

from .bootstrap_origin import bootstrap_origin
from .device_certificate_verify import VerifiedDeviceCredential, verify_device_delivery
from .device_csr import device_certificate_operation_id
from .errors import OnboardingClientError
from .http_response import request_json
from .setup_proofs import sign_setup_request


@dataclass(frozen=True)
class DeviceCertificateDelivery:
    status: str
    operation_id: UUID | None
    credential: VerifiedDeviceCredential | None


async def fetch_device_certificate(*, origin, draft, receipt, store, trust) -> DeviceCertificateDelivery:
    trusted = bootstrap_origin(origin)
    saved = store.require_saved(draft, expected_origin=trusted)
    if trust.platform_origin != trusted:
        raise OnboardingClientError("onboarding_device_trust_invalid")
    path = f"/api/client-onboarding/sessions/{saved.operation_id}/certificate"
    headers = sign_setup_request(private_key=saved.keys.proof_key,
        operation_id=saved.operation_id, method="GET", path=path)
    async with httpx.AsyncClient(http2=True, trust_env=False, follow_redirects=False, timeout=15) as client:
        result = await request_json(client, method="GET", url=trusted + path,
            headers=headers, error_prefix="onboarding")
    operation = device_certificate_operation_id(saved.operation_id)
    if result == {"status": "awaiting_csr"}:
        return DeviceCertificateDelivery("awaiting_csr", None, None)
    if (set(result) == {"operation_id", "status"} and result["operation_id"] == str(operation)
        and isinstance(result["status"], str) and result["status"] in {"pending", "in_progress", "failed", "cancelled"}):
        return DeviceCertificateDelivery(result["status"], operation, None)
    credential = verify_device_delivery(draft=saved, receipt=receipt, trust=trust, payload=result)
    return DeviceCertificateDelivery(result["status"], operation, credential)
