# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Approved native CSR claim: device mTLS and original setup-bound proof."""

from hashlib import sha256
import re
from uuid import UUID

from cryptography.hazmat.primitives import serialization

from .atomic_private_file import read_private_file
from .certificate_material import parse_certificates
from .device_http import create_device_http_client
from .device_identity import AdmittedDevice
from .errors import OnboardingClientError
from .http_response import request_json
from .origins import https_origin
from .proofs import canonical_json
from .service_account_csr import service_account_csr_facts
from .setup_proofs import sign_setup_request


def native_claim_proof_body(*, device, certificate_sha256, request_id, intent_sha256, csr):
    return {"tenant_id": str(device.tenant_id), "device_id": str(device.device_id),
        "request_id": str(request_id), "revision": 1, "intent_sha256": intent_sha256,
        "device_certificate_sha256": certificate_sha256,
        "tls_public_key_sha256": csr.public_key_sha256, "csr_sha256": csr.csr_sha256}


class NativeClaimClient:
    def __init__(self, *, origin: str, device: AdmittedDevice, setup_id: UUID):
        if not isinstance(device, AdmittedDevice) or not isinstance(setup_id, UUID):
            raise ValueError("exact admitted device and original setup required")
        self.origin, self.device, self.setup_id = https_origin(origin), device, setup_id
        self._http = create_device_http_client(tenant_id=device.tenant_id, device_id=device.device_id,
            certificate_path=device.certificate_path, private_key_path=device.private_key_path)

    async def stage(self, *, request_id: UUID, intent_sha256: str, csr_pem: str):
        if (not isinstance(request_id, UUID) or not isinstance(intent_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", intent_sha256)):
            raise ValueError("exact reviewed request required")
        facts = service_account_csr_facts(csr_pem)
        if facts.tenant_id != self.device.tenant_id:
            raise OnboardingClientError("onboarding_tenant_mismatch")
        leaf = parse_certificates(read_private_file(self.device.certificate_path, limit=65536).decode("ascii"), maximum=9)[0]
        device_key_digest = sha256(leaf.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
        if facts.public_key_sha256 == device_key_digest:
            raise OnboardingClientError("onboarding_integration_key_not_separate")
        body = native_claim_proof_body(device=self.device, request_id=request_id, intent_sha256=intent_sha256,
            certificate_sha256=sha256(leaf.public_bytes(serialization.Encoding.DER)).hexdigest(), csr=facts)
        path = f"/api/client-onboarding/requests/{request_id}/native-claim"
        headers = sign_setup_request(private_key=self.device.proof_key, operation_id=self.setup_id,
            method="POST", path=path, body=body)
        headers["Content-Type"] = "application/json"
        result = await request_json(self._http, method="POST", url=self.origin + path, headers=headers,
            content=canonical_json({"revision": 1, "intent_sha256": intent_sha256, "csr_pem": facts.pem}),
            error_prefix="onboarding")
        if (set(result) != {"request_id", "status", "revision", "intent_sha256"}
            or result["request_id"] != str(request_id) or result["status"] != "issuing"
            or type(result["revision"]) is not int or result["revision"] != 1
            or result["intent_sha256"] != intent_sha256):
            raise OnboardingClientError("onboarding_response_invalid")

    async def __aenter__(self):
        return self

    async def certificate(self, *, platform_origin, assignment, csr_pem, trust):
        from .native_certificate_delivery import fetch_native_certificate
        return await fetch_native_certificate(self._http, platform_origin=platform_origin, origin=self.origin, device=self.device,
            setup_id=self.setup_id, assignment=assignment, csr_pem=csr_pem, trust=trust)

    async def __aexit__(self, *_args):
        await self._http.aclose()
