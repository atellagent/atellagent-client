# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Data contracts shared by the enrollment implementation modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric import rsa


class CertificateEnrollmentError(RuntimeError):
    """Enrollment failed without persisting the one-time capability."""


@dataclass(frozen=True)
class CertificateEnrollmentProfile:
    service_account_id: str
    tenant_id: str
    enrollment_url: str
    expires_at: datetime

    @property
    def subject_dns(self) -> str:
        """Return the sole non-routable DNS SAN for this client certificate."""
        return (
            f"sa.{UUID(self.service_account_id).hex}."
            f"{UUID(self.tenant_id).hex}.identity.invalid"
        )


@dataclass(frozen=True)
class CertificateEnrollmentResult:
    certificate_path: Path
    private_key_path: Path
    certificate_expires_at: datetime
    operation_id: str


@dataclass(frozen=True)
class PreparedCertificateRotation:
    profile: CertificateEnrollmentProfile
    private_key: rsa.RSAPrivateKey = field(repr=False)
    csr_pem: str
    expected_common_name: str
    certificate_path: Path
    private_key_path: Path


@dataclass(frozen=True)
class StagedCertificateRotation:
    prepared: PreparedCertificateRotation
    staged_certificate_path: Path
    staged_private_key_path: Path
    certificate_expires_at: datetime


__all__ = [
    "CertificateEnrollmentError",
    "CertificateEnrollmentProfile",
    "CertificateEnrollmentResult",
    "PreparedCertificateRotation",
    "StagedCertificateRotation",
]
