# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Committed candidate credentials are not an admission/activation receipt."""

from dataclasses import dataclass, field
from pathlib import Path

from .device_certificate_verify import VerifiedDeviceCredential
from .device_csr import device_certificate_operation_id


@dataclass(frozen=True)
class StoredDeviceCredential:
    credential: VerifiedDeviceCredential
    certificate_path: Path = field(repr=False)
    private_key_path: Path = field(repr=False)


def delivery_payload(draft, credential) -> dict:
    return {"operation_id": str(device_certificate_operation_id(draft.operation_id)),
        "status": "awaiting_activation", "device_id": str(credential.device_id),
        "certificate_pem": credential.certificate_pem,
        "certificate_chain_pem": credential.certificate_chain_pem,
        "certificate_sha256": credential.certificate_sha256,
        "expires_at": credential.expires_at.isoformat()}
