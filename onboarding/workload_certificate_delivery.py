# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Proof-authenticated leaf delivery with independently obtained signing trust."""

import base64

from .http_response import request_json
from .service_account_certificate_verify import verify_service_account_delivery


async def fetch_workload_certificate(client, *, csr_pem, trust):
    trust.validated(expected_origin=client.origin)
    facts = client.csr_facts(csr_pem)
    path = f"/api/client-onboarding/requests/{client.receipt.id}/certificate"
    payload = await request_json(client._http, method="GET", url=client.origin + path,
        headers=client.headers("GET", path), error_prefix="onboarding")
    if payload == {"status": "issuing"}:
        return None
    return verify_service_account_delivery(platform_origin=client.origin,
        tenant_id=client.draft.setup.tenant_id, service_account_id=client.receipt.service_account_id,
        integration_id=client.receipt.integration_id, csr_pem=facts.pem,
        approved_public_key_der=base64.b64decode(
            client.draft.setup.proposal()["tls_public_key_der_base64"], validate=True),
        trust=trust, payload=payload)
