# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""CSR construction and issued leaf validation for local certificate rotation."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .certificate_errors import CertificateMaterialError
from .certificate_chain import validate_chain
from .certificate_identity import ServiceAccountCertificateIdentity
from atellagent_client.onboarding.certificate_material import parse_certificates


def public_key_sha256(public_key: Any) -> str:
    encoded = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(encoded).hexdigest()


def build_csr(
    profile: ServiceAccountCertificateIdentity,
) -> tuple[rsa.RSAPrivateKey, x509.CertificateSigningRequest, str]:
    """Generate the reviewed RSA-2048 proof-of-possession request."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key_digest = public_key_sha256(private_key.public_key())
    common_name = f"sa:{profile.service_account_id}:{public_key_digest[:16]}"
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(profile.subject_dns)]),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    return private_key, csr, common_name


def _certificate_time(certificate: x509.Certificate, field: str) -> datetime:
    utc_value = getattr(certificate, f"{field}_utc", None)
    value = utc_value if utc_value is not None else getattr(certificate, field)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_issued_certificate(
    *,
    certificate_pem: str,
    chain_pem: str,
    private_key: rsa.RSAPrivateKey,
    profile: ServiceAccountCertificateIdentity,
    expected_common_name: str,
    trusted_ca_pem: str,
) -> tuple[str, datetime]:
    """Verify the issued leaf, chain, and identity before any credential write."""
    try:
        certificate = parse_certificates(certificate_pem, maximum=1)[0]
    except (ValueError, TypeError, UnicodeError) as exc:
        raise CertificateMaterialError("Certificate response certificate is invalid") from exc

    if public_key_sha256(certificate.public_key()) != public_key_sha256(
        private_key.public_key()
    ):
        raise CertificateMaterialError(
            "Certificate response certificate does not match the local private key"
        )
    expected_subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, expected_common_name)]
    )
    if certificate.subject != expected_subject:
        raise CertificateMaterialError("Certificate response certificate subject is invalid")
    try:
        san = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        eku = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        constraints = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
    except x509.ExtensionNotFound as exc:
        raise CertificateMaterialError(
            "Certificate response certificate lacks a required constraint"
        ) from exc
    dns_names = [value.value for value in san if isinstance(value, x509.DNSName)]
    if len(san) != 1 or dns_names != [profile.subject_dns]:
        raise CertificateMaterialError("Certificate response certificate DNS identity is invalid")
    if set(eku) != {ExtendedKeyUsageOID.CLIENT_AUTH}:
        raise CertificateMaterialError("Certificate response certificate usage is invalid")
    if constraints.ca or constraints.path_length is not None:
        raise CertificateMaterialError("Certificate response certificate cannot be a CA")

    now = datetime.now(timezone.utc)
    not_before = _certificate_time(certificate, "not_valid_before")
    not_after = _certificate_time(certificate, "not_valid_after")
    if now < not_before or now >= not_after or not_after - not_before > timedelta(days=90, minutes=20):
        raise CertificateMaterialError("Certificate response certificate is not currently valid")

    validate_chain(certificate, chain_pem, trusted_ca_pem)
    return str(certificate_pem).strip() + "\n" + str(chain_pem).strip() + "\n", not_after


__all__ = ["build_csr", "validate_issued_certificate"]
