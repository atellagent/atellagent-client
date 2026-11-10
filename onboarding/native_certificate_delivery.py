# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Device-authenticated native delivery; independent trust precedes leaf acceptance."""

import base64
from uuid import UUID

from .device_assignments import DeviceAssignment
from .errors import OnboardingClientError
from .http_response import request_json
from .service_account_certificate_verify import verify_service_account_delivery
from .service_account_trust import ServiceAccountTrustBundle
from .setup_proofs import sign_setup_request


async def fetch_native_certificate(http, *, platform_origin, origin, device, setup_id, assignment: DeviceAssignment,
                                   csr_pem, trust: ServiceAccountTrustBundle):
    if (not isinstance(assignment, DeviceAssignment) or not isinstance(setup_id, UUID)
            or assignment.onboarding_session_id != setup_id
            or assignment.status not in ("issuing", "issued")):
        raise OnboardingClientError("onboarding_native_assignment_invalid")
    trust.validated(expected_origin=platform_origin)
    if platform_origin != trust.platform_origin or origin != trust.service_account_api_origin:
        raise OnboardingClientError("onboarding_service_account_trust_invalid")
    path = f"/api/client-onboarding/requests/{assignment.request_id}/native-certificate"
    headers = sign_setup_request(private_key=device.proof_key, operation_id=setup_id,
                                method="GET", path=path, body=None)
    payload = await request_json(http, method="GET", url=origin + path, headers=headers,
                                 error_prefix="onboarding")
    if payload == {"status": "issuing"}:
        return None  # Progress only; no credential readiness.
    return verify_service_account_delivery(platform_origin=platform_origin,
        tenant_id=device.tenant_id, service_account_id=assignment.service_account_id,
        integration_id=assignment.integration_id, csr_pem=csr_pem,
        approved_public_key_der=base64.b64decode(assignment.proposal.tls_public_key_der_base64, validate=True),
        trust=trust, payload=payload)
