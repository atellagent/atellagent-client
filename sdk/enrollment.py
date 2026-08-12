# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Local-key CSR enrollment for an Atellagent service account."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import UUID

import httpx
import yaml
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


def _required_uuid(value: Any, label: str) -> str:
    try:
        return str(UUID(str(value or "").strip()))
    except (ValueError, TypeError, AttributeError) as exc:
        raise CertificateEnrollmentError(f"{label} must be a UUID") from exc


def _parse_timestamp(value: Any, label: str) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CertificateEnrollmentError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_certificate_enrollment_profile(
    config_path: os.PathLike[str] | str,
) -> CertificateEnrollmentProfile:
    """Read only the public identity fields needed to construct the CSR."""
    try:
        with Path(config_path).open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise CertificateEnrollmentError(
            "Unable to read enrollment configuration"
        ) from exc
    if not isinstance(data, dict):
        raise CertificateEnrollmentError(
            "Enrollment configuration must be a YAML object"
        )

    service_account_id = _required_uuid(
        data.get("service_account_id"), "service_account_id"
    )
    tenant_id = _required_uuid(data.get("tenant_id"), "tenant_id")
    enrollment_url = str(data.get("certificate_enrollment_url") or "").strip()
    parsed_url = urlparse(enrollment_url)
    if (
        parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
        or parsed_url.fragment
    ):
        raise CertificateEnrollmentError(
            "certificate_enrollment_url must be a public HTTPS URL"
        )
    expires_at = _parse_timestamp(
        data.get("certificate_enrollment_expires_at"),
        "certificate_enrollment_expires_at",
    )
    if expires_at is None:
        raise CertificateEnrollmentError(
            "certificate_enrollment_expires_at is required"
        )
    return CertificateEnrollmentProfile(
        service_account_id=service_account_id,
        tenant_id=tenant_id,
        enrollment_url=enrollment_url,
        expires_at=expires_at,
    )


def _public_key_sha256(public_key: Any) -> str:
    encoded = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(encoded).hexdigest()


def _build_csr(
    profile: CertificateEnrollmentProfile,
) -> tuple[rsa.RSAPrivateKey, x509.CertificateSigningRequest, str]:
    # The enrollment profile accepts RSA-2048 CSRs only. Keep the client and
    # issuer on that exact reviewed profile rather than failing remotely.
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key_digest = _public_key_sha256(private_key.public_key())
    common_name = f"sa:{profile.service_account_id}:{public_key_digest[:16]}"
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(profile.subject_dns)]
            ),
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
        signature_padding = certificate.signature_algorithm_parameters
        if signature_padding is None:
            signature_padding = padding.PKCS1v15()
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
            raise CertificateEnrollmentError(
                "Enrollment response lacks a certificate chain"
            )
        return list(x509.load_pem_x509_certificates(raw))
    except CertificateEnrollmentError:
        raise
    except (ValueError, UnicodeError) as exc:
        raise CertificateEnrollmentError(
            "Enrollment response certificate chain is invalid"
        ) from exc


def _validate_issued_certificate(
    *,
    certificate_pem: str,
    chain_pem: str,
    private_key: rsa.RSAPrivateKey,
    profile: CertificateEnrollmentProfile,
    expected_common_name: str,
) -> tuple[str, datetime]:
    try:
        certificate = x509.load_pem_x509_certificate(
            str(certificate_pem or "").strip().encode("ascii", errors="strict")
        )
    except (ValueError, UnicodeError) as exc:
        raise CertificateEnrollmentError(
            "Enrollment response certificate is invalid"
        ) from exc

    if _public_key_sha256(certificate.public_key()) != _public_key_sha256(
        private_key.public_key()
    ):
        raise CertificateEnrollmentError(
            "Enrollment response certificate does not match the local private key"
        )
    expected_subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, expected_common_name)]
    )
    if certificate.subject != expected_subject:
        raise CertificateEnrollmentError(
            "Enrollment response certificate subject is invalid"
        )
    try:
        san = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        eku = certificate.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage
        ).value
        constraints = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
    except x509.ExtensionNotFound as exc:
        raise CertificateEnrollmentError(
            "Enrollment response certificate lacks a required constraint"
        ) from exc
    dns_names = [
        value.value
        for value in san
        if isinstance(value, x509.DNSName)
    ]
    if len(san) != 1 or dns_names != [profile.subject_dns]:
        raise CertificateEnrollmentError(
            "Enrollment response certificate DNS identity is invalid"
        )
    if set(eku) != {ExtendedKeyUsageOID.CLIENT_AUTH}:
        raise CertificateEnrollmentError(
            "Enrollment response certificate usage is invalid"
        )
    if constraints.ca or constraints.path_length is not None:
        raise CertificateEnrollmentError(
            "Enrollment response certificate cannot be a CA"
        )

    now = datetime.now(timezone.utc)
    not_before = _certificate_time(certificate, "not_valid_before")
    not_after = _certificate_time(certificate, "not_valid_after")
    if now < not_before or now >= not_after:
        raise CertificateEnrollmentError(
            "Enrollment response certificate is not currently valid"
        )

    chain = _parse_chain(chain_pem)
    leaf_fingerprint = certificate.fingerprint(hashes.SHA256())
    chain = [
        item for item in chain if item.fingerprint(hashes.SHA256()) != leaf_fingerprint
    ]
    if not chain:
        raise CertificateEnrollmentError(
            "Enrollment response lacks an issuing certificate"
        )
    child = certificate
    remaining = list(chain)
    root_certificate = None
    while remaining:
        issuer = next(
            (item for item in remaining if item.subject == child.issuer), None
        )
        if issuer is None:
            raise CertificateEnrollmentError(
                "Enrollment response certificate chain is incomplete"
            )
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

    combined = str(certificate_pem).strip() + "\n" + str(chain_pem).strip() + "\n"
    return combined, not_after


def _credential_paths(
    config_path: os.PathLike[str] | str,
    certificate_path: Optional[os.PathLike[str] | str],
    private_key_path: Optional[os.PathLike[str] | str],
) -> tuple[Path, Path]:
    config_dir = Path(config_path).expanduser().resolve().parent
    certificate = Path(
        certificate_path
        or os.getenv("ATELLAGENT_CERT_PATH")
        or config_dir / "certs" / "client-cert.pem"
    ).expanduser()
    private_key = Path(
        private_key_path
        or os.getenv("ATELLAGENT_KEY_PATH")
        or config_dir / "certs" / "client-key.pem"
    ).expanduser()
    if certificate.resolve() == private_key.resolve():
        raise CertificateEnrollmentError("Credential output paths must be distinct")
    return certificate, private_key


def _stage_file(path: Path, content: bytes, mode: int) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(staged_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return staged
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        staged.unlink(missing_ok=True)
        raise


def _commit_credential_set(
    *,
    certificate_path: Path,
    certificate_pem: bytes,
    private_key_path: Path,
    private_key_pem: bytes,
    replace: bool,
) -> None:
    for path in (certificate_path, private_key_path):
        if path.exists() and not replace:
            raise CertificateEnrollmentError(
                f"Credential path already exists: {path}; use explicit rotation replacement"
            )
    staged_certificate = _stage_file(certificate_path, certificate_pem, 0o644)
    staged_private_key = _stage_file(private_key_path, private_key_pem, 0o600)
    try:
        if replace:
            os.replace(staged_certificate, certificate_path)
            os.replace(staged_private_key, private_key_path)
        else:
            os.link(staged_certificate, certificate_path)
            try:
                os.link(staged_private_key, private_key_path)
            except Exception:
                certificate_path.unlink(missing_ok=True)
                private_key_path.unlink(missing_ok=True)
                raise
            staged_certificate.unlink()
            staged_private_key.unlink()
        os.chmod(certificate_path, 0o644)
        os.chmod(private_key_path, 0o600)
    except CertificateEnrollmentError:
        raise
    except Exception as exc:
        raise CertificateEnrollmentError(
            "Unable to store enrolled credentials"
        ) from exc
    finally:
        staged_certificate.unlink(missing_ok=True)
        staged_private_key.unlink(missing_ok=True)


def prepare_certificate_rotation(
    *,
    service_account_id: str,
    tenant_id: str,
    certificate_path: os.PathLike[str] | str,
    private_key_path: os.PathLike[str] | str,
) -> PreparedCertificateRotation:
    """Generate one replacement key and proof-of-possession CSR locally."""
    profile = CertificateEnrollmentProfile(
        service_account_id=_required_uuid(service_account_id, "service_account_id"),
        tenant_id=_required_uuid(tenant_id, "tenant_id"),
        enrollment_url="https://runtime-identity-rotation.invalid",
        expires_at=datetime.max.replace(tzinfo=timezone.utc),
    )
    private_key, csr, common_name = _build_csr(profile)
    return PreparedCertificateRotation(
        profile=profile,
        private_key=private_key,
        csr_pem=csr.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        expected_common_name=common_name,
        certificate_path=Path(certificate_path).expanduser(),
        private_key_path=Path(private_key_path).expanduser(),
    )


def stage_certificate_rotation(
    prepared: PreparedCertificateRotation,
    *,
    certificate_pem: str,
    certificate_chain_pem: str,
) -> StagedCertificateRotation:
    """Validate and fsync replacement material before server-side activation."""
    combined_certificate, expires_at = _validate_issued_certificate(
        certificate_pem=certificate_pem,
        chain_pem=certificate_chain_pem,
        private_key=prepared.private_key,
        profile=prepared.profile,
        expected_common_name=prepared.expected_common_name,
    )
    private_key_pem = prepared.private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    staged_certificate = _stage_file(
        prepared.certificate_path,
        combined_certificate.encode("ascii"),
        0o644,
    )
    try:
        staged_private_key = _stage_file(
            prepared.private_key_path,
            private_key_pem,
            0o600,
        )
    except Exception:
        staged_certificate.unlink(missing_ok=True)
        raise
    return StagedCertificateRotation(
        prepared=prepared,
        staged_certificate_path=staged_certificate,
        staged_private_key_path=staged_private_key,
        certificate_expires_at=expires_at,
    )


def commit_staged_certificate_rotation(staged: StagedCertificateRotation) -> None:
    """Install a fully staged key/certificate pair after cluster activation."""
    try:
        os.replace(
            staged.staged_certificate_path,
            staged.prepared.certificate_path,
        )
        os.replace(
            staged.staged_private_key_path,
            staged.prepared.private_key_path,
        )
        os.chmod(staged.prepared.certificate_path, 0o644)
        os.chmod(staged.prepared.private_key_path, 0o600)
    except Exception as exc:
        raise CertificateEnrollmentError(
            "Activated certificate rotation could not install staged credentials"
        ) from exc


def discard_staged_certificate_rotation(staged: StagedCertificateRotation) -> None:
    staged.staged_certificate_path.unlink(missing_ok=True)
    staged.staged_private_key_path.unlink(missing_ok=True)


async def enroll_service_account_certificate(
    *,
    config_path: os.PathLike[str] | str,
    enrollment_token: str,
    certificate_path: Optional[os.PathLike[str] | str] = None,
    private_key_path: Optional[os.PathLike[str] | str] = None,
    replace: bool = False,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> CertificateEnrollmentResult:
    """Generate the private key locally, enroll its CSR, and persist only locally."""
    token = str(enrollment_token or "").strip()
    if len(token) < 40 or len(token) > 512:
        raise CertificateEnrollmentError("Enrollment token is invalid")
    profile = load_certificate_enrollment_profile(config_path)
    if datetime.now(timezone.utc) >= profile.expires_at:
        raise CertificateEnrollmentError("Enrollment token has expired")
    certificate_target, private_key_target = _credential_paths(
        config_path,
        certificate_path,
        private_key_path,
    )
    for path in (certificate_target, private_key_target):
        if path.exists() and not replace:
            raise CertificateEnrollmentError(
                f"Credential path already exists: {path}; use explicit rotation replacement"
            )

    private_key, csr, common_name = _build_csr(profile)
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("ascii")
    private_key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    delay_seconds = 1.0
    timeout = httpx.Timeout(15.0, connect=10.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        trust_env=False,
        transport=transport,
    ) as client:
        while True:
            if datetime.now(timezone.utc) >= profile.expires_at:
                raise CertificateEnrollmentError(
                    "Enrollment token expired during issuance"
                )
            try:
                response = await client.post(
                    profile.enrollment_url,
                    json={"token": token, "csr_pem": csr_pem},
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                response = None
                last_error = exc
            else:
                last_error = None

            if response is not None and response.status_code == 200:
                try:
                    payload = response.json()
                    certificate_payload = payload["certificate"]
                    certificate_pem = certificate_payload["certificate_pem"]
                    chain_pem = certificate_payload["certificate_chain_pem"]
                    operation_id = str(payload["operation_id"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise CertificateEnrollmentError(
                        "Enrollment response is missing required certificate fields"
                    ) from exc
                combined_certificate, expires_at = _validate_issued_certificate(
                    certificate_pem=certificate_pem,
                    chain_pem=chain_pem,
                    private_key=private_key,
                    profile=profile,
                    expected_common_name=common_name,
                )
                _commit_credential_set(
                    certificate_path=certificate_target,
                    certificate_pem=combined_certificate.encode("ascii"),
                    private_key_path=private_key_target,
                    private_key_pem=private_key_pem,
                    replace=replace,
                )
                return CertificateEnrollmentResult(
                    certificate_path=certificate_target,
                    private_key_path=private_key_target,
                    certificate_expires_at=expires_at,
                    operation_id=operation_id,
                )

            if response is not None and response.status_code in {409, 410}:
                label = (
                    "failed"
                    if response.status_code == 409
                    else "expired or already used"
                )
                raise CertificateEnrollmentError(f"Certificate enrollment {label}")
            retryable = (
                response is None
                or response.status_code == 202
                or (response.status_code in {429, 502, 503, 504})
            )
            if not retryable:
                status = response.status_code if response is not None else "unavailable"
                raise CertificateEnrollmentError(
                    f"Certificate enrollment was rejected (HTTP {status})"
                ) from last_error

            sleep_for = delay_seconds
            remaining = (
                profile.expires_at - datetime.now(timezone.utc)
            ).total_seconds()
            if remaining <= 0:
                raise CertificateEnrollmentError(
                    "Enrollment token expired during issuance"
                )
            sleep_for = min(sleep_for, remaining)
            await asyncio.sleep(sleep_for)
            delay_seconds = min(delay_seconds * 2.0, 8.0)


__all__ = [
    "CertificateEnrollmentError",
    "CertificateEnrollmentProfile",
    "CertificateEnrollmentResult",
    "PreparedCertificateRotation",
    "StagedCertificateRotation",
    "commit_staged_certificate_rotation",
    "discard_staged_certificate_rotation",
    "enroll_service_account_certificate",
    "load_certificate_enrollment_profile",
    "prepare_certificate_rotation",
    "stage_certificate_rotation",
]
