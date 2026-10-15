# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public issued material reconstructed for independent verification at rest."""


def service_account_delivery_payload(credential):
    return {"operation_id": str(credential.operation_id), "status": "awaiting_activation",
        "service_account_id": str(credential.service_account_id), "integration_id": str(credential.integration_id),
        "certificate_pem": credential.certificate_pem, "certificate_chain_pem": credential.certificate_chain_pem,
        "certificate_sha256": credential.certificate_sha256, "expires_at": credential.expires_at.isoformat()}
