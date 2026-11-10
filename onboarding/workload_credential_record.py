# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""An issued workload credential candidate is not activated runtime authority."""

import base64
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from .service_account_certificate_verify import VerifiedServiceAccountCredential, verify_service_account_delivery
from .service_account_csr import service_account_csr


@dataclass(frozen=True)
class StoredWorkloadCredential:
    credential: VerifiedServiceAccountCredential
    certificate_path: Path = field(repr=False)
    private_key_path: Path = field(repr=False)


def workload_key_pem(draft):
    return draft.setup.keys.tls_key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def verify_workload_credential(draft, receipt, trust, payload, *, verification_time=None):
    setup = draft.setup
    return verify_service_account_delivery(platform_origin=setup.platform_origin, tenant_id=setup.tenant_id,
        service_account_id=receipt.service_account_id, integration_id=receipt.integration_id,
        csr_pem=service_account_csr(setup.keys.tls_key, tenant_id=setup.tenant_id,
                                  service_account_id=receipt.service_account_id),
        approved_public_key_der=base64.b64decode(setup.proposal()["tls_public_key_der_base64"], validate=True),
        trust=trust, payload=payload, verification_time=verification_time)


def workload_credential_metadata(draft, receipt, credential):
    setup = draft.setup
    return {"revision": 1, "request_id": str(draft.proposal.id), "setup_id": str(setup.operation_id),
        "tenant_id": str(setup.tenant_id), "setup_operator_id": str(setup.owner_user_id),
        "platform_origin": setup.platform_origin, "draft_sha256": sha256(draft.encode()).hexdigest(),
        "intent_sha256": receipt.intent_sha256, "operation_id": str(credential.operation_id),
        "service_account_id": str(credential.service_account_id), "integration_id": str(credential.integration_id),
        "certificate_sha256": credential.certificate_sha256, "expires_at": credential.expires_at.isoformat()}
