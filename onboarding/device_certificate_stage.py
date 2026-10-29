# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Approval-backed CSR staging; a queued operation is not a usable certificate."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import httpx

from .bootstrap_origin import bootstrap_origin
from .contract_values import aware_time, canonical_id
from .device_csr import device_certificate_operation_id, device_csr
from .errors import OnboardingClientError
from .http_response import request_json
from .proofs import canonical_json
from .setup_proofs import sign_setup_request


@dataclass(frozen=True)
class DeviceCertificateStage:
    operation_id: UUID
    status: str
    expires_at: datetime


async def stage_device_certificate(*, origin, draft, receipt, store) -> DeviceCertificateStage:
    trusted = bootstrap_origin(origin)
    saved = store.require_saved(draft, expected_origin=trusted)
    operation = device_certificate_operation_id(saved.operation_id)
    csr = device_csr(saved, receipt)
    path = f"/api/client-onboarding/sessions/{saved.operation_id}/certificate"
    body = {"operation_id": str(operation), "csr_pem": csr}
    signed = sign_setup_request(private_key=saved.keys.proof_key, operation_id=saved.operation_id,
        method="POST", path=path, body=body)
    proof = {name: signed[f"X-Atellagent-Onboarding-{name.title()}"]
        for name in ("timestamp", "nonce", "signature")}
    async with httpx.AsyncClient(http2=True, trust_env=False, follow_redirects=False, timeout=15) as client:
        result = await request_json(client, method="POST", url=trusted + path,
            headers={"Content-Type": "application/json"},
            content=canonical_json({**body, "proof": proof}), error_prefix="onboarding")
    try:
        if (set(result) != {"operation_id", "status", "expires_at"}
            or result["operation_id"] != str(operation)
            or result["status"] not in {"pending", "in_progress", "issued", "succeeded", "failed", "cancelled"}):
            raise ValueError("exact certificate stage required")
        return DeviceCertificateStage(canonical_id(result["operation_id"]), result["status"], aware_time(result["expires_at"]))
    except (ValueError, TypeError, KeyError) as exc:
        raise OnboardingClientError("onboarding_response_invalid") from exc
