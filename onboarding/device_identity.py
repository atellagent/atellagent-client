# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Exact admitted-device material; never interchangeable with an agent account."""

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@dataclass(frozen=True)
class AdmittedDevice:
    tenant_id: UUID
    device_id: UUID
    owner_user_id: UUID
    proof_key: Ed25519PrivateKey = field(repr=False)
    certificate_path: Path = field(repr=False)
    private_key_path: Path = field(repr=False)

    def __post_init__(self):
        if (not all(isinstance(value, UUID) for value in (self.tenant_id, self.device_id, self.owner_user_id))
            or not isinstance(self.proof_key, Ed25519PrivateKey)
            or not all(isinstance(value, Path) for value in (self.certificate_path, self.private_key_path))):
            raise ValueError("exact admitted device identity and credentials required")
