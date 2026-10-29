"""Locally prepared and staged renewal material; no enrollment URL/token."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa
from .certificate_identity import ServiceAccountCertificateIdentity


@dataclass(frozen=True)
class PreparedCertificateRotation:
    profile: ServiceAccountCertificateIdentity
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
    candidate_pointer: Path
    public_key_sha256: str
    certificate_expires_at: datetime
