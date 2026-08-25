# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""CSR construction and issuer-response validation for enrollment."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import (
    dsa,
    ec,
    ed25519,
    ed448,
    padding,
    rsa,
)
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .enrollment_types import CertificateEnrollmentError, CertificateEnrollmentProfile


def public_key_sha256(public_key: Any) -> str:
    encoded = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(encoded).hexdigest()


def build_csr(
    profile: CertificateEnrollmentProfile,
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


def _verify_certificate_signature(
    certificate: x509.Certificate,
    issuer: x509.Certificate,
) -> None:
    public_key = issuer.public_key()
    signature_hash = certificate.signature_hash_algorithm
    if isinstance(public_key, rsa.RSAPublicKey):
        signature_padding = certificate.signature_algorithm_parameters or padding.PKCS1v15()
        public_key.verify(
            certificate.signature,
            certificate.tbs_certificate_bytes,
            signature_padding,
            signature_hash,
        )
    elif isinstance(public_key, ec.EllipticCurvePublicKey):
        public_key.verify(
            certificate.signature,
            certificate.tbs_certificate_bytes,
            ec.ECDSA(signature_hash),
        )
    elif isinstance(public_key, dsa.DSAPublicKey):
        public_key.verify(
            certificate.signature,
            certificate.tbs_certificate_bytes,
            signature_hash,
        )
    elif isinstance(public_key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
        public_key.verify(certificate.signature, certificate.tbs_certificate_bytes)
    else:
        raise CertificateEnrollmentError(
            "Certificate chain uses an unsupported key type"
        )


def _parse_chain(chain_pem: str) -> list[x509.Certificate]:
    try:
        raw = str(chain_pem or "").strip().encode("ascii", errors="strict")
        if not raw:
            raise CertificateEnrollmentError("Enrollment response lacks a certificate chain")
        return list(x509.load_pem_x509_certificates(raw))
    except CertificateEnrollmentError:
        raise
    except (ValueError, UnicodeError) as exc:
        raise CertificateEnrollmentError(
            "Enrollment response certificate chain is invalid"
        ) from exc


def validate_issued_certificate(
    *,
    certificate_pem: str,
    chain_pem: str,
    private_key: rsa.RSAPrivateKey,
    profile: CertificateEnrollmentProfile,
    expected_common_name: str,
) -> tuple[str, datetime]:
    """Verify the issued leaf, chain, and identity before any credential write."""
    try:
        certificate = x509.load_pem_x509_certificate(
            str(certificate_pem or "").strip().encode("ascii", errors="strict")
        )
    except (ValueError, UnicodeError) as exc:
        raise CertificateEnrollmentError("Enrollment response certificate is invalid") from exc

    if public_key_sha256(certificate.public_key()) != public_key_sha256(
        private_key.public_key()
    ):
        raise CertificateEnrollmentError(
            "Enrollment response certificate does not match the local private key"
        )
    expected_subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, expected_common_name)]
    )
    if certificate.subject != expected_subject:
        raise CertificateEnrollmentError("Enrollment response certificate subject is invalid")
    try:
        san = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        eku = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        constraints = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
    except x509.ExtensionNotFound as exc:
        raise CertificateEnrollmentError(
            "Enrollment response certificate lacks a required constraint"
        ) from exc
    dns_names = [value.value for value in san if isinstance(value, x509.DNSName)]
    if len(san) != 1 or dns_names != [profile.subject_dns]:
        raise CertificateEnrollmentError("Enrollment response certificate DNS identity is invalid")
    if set(eku) != {ExtendedKeyUsageOID.CLIENT_AUTH}:
        raise CertificateEnrollmentError("Enrollment response certificate usage is invalid")
    if constraints.ca or constraints.path_length is not None:
        raise CertificateEnrollmentError("Enrollment response certificate cannot be a CA")

    now = datetime.now(timezone.utc)
    not_before = _certificate_time(certificate, "not_valid_before")
    not_after = _certificate_time(certificate, "not_valid_after")
    if now < not_before or now >= not_after:
        raise CertificateEnrollmentError("Enrollment response certificate is not currently valid")

    leaf_fingerprint = certificate.fingerprint(hashes.SHA256())
    remaining = [
        item
        for item in _parse_chain(chain_pem)
        if item.fingerprint(hashes.SHA256()) != leaf_fingerprint
    ]
    if not remaining:
        raise CertificateEnrollmentError("Enrollment response lacks an issuing certificate")
    child = certificate
    root_certificate = None
    while remaining:
        issuer = next((item for item in remaining if item.subject == child.issuer), None)
        if issuer is None:
            raise CertificateEnrollmentError("Enrollment response certificate chain is incomplete")
        try:
            issuer_constraints = issuer.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value
            if not issuer_constraints.ca:
                raise CertificateEnrollmentError(
                    "Enrollment response chain contains a non-CA issuer"
                )
            _verify_certificate_signature(child, issuer)
        except CertificateEnrollmentError:
            raise
        except Exception as exc:
            raise CertificateEnrollmentError(
                "Enrollment response certificate chain signature is invalid"
            ) from exc
        remaining.remove(issuer)
        child = issuer
        if child.subject == child.issuer:
            if remaining:
                raise CertificateEnrollmentError(
                    "Enrollment response certificate chain contains unrelated certificates"
                )
            try:
                _verify_certificate_signature(child, child)
            except Exception as exc:
                raise CertificateEnrollmentError(
                    "Enrollment response root certificate signature is invalid"
                ) from exc
            root_certificate = child
            break

    if root_certificate is None:
        raise CertificateEnrollmentError(
            "Enrollment response certificate chain lacks its public trust root"
        )
    return str(certificate_pem).strip() + "\n" + str(chain_pem).strip() + "\n", not_after


__all__ = ["build_csr", "validate_issued_certificate"]
