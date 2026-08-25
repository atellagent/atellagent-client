# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Local-key CSR enrollment for an Atellagent service account."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import UUID

import httpx
import yaml
from cryptography.hazmat.primitives import serialization

from .enrollment_certificates import build_csr, validate_issued_certificate
from .enrollment_storage import commit_credential_set, credential_paths, stage_file
from .enrollment_types import (
    CertificateEnrollmentError,
    CertificateEnrollmentProfile,
    CertificateEnrollmentResult,
    PreparedCertificateRotation,
    StagedCertificateRotation,
)


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
    private_key, csr, common_name = build_csr(profile)
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
    combined_certificate, expires_at = validate_issued_certificate(
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
    staged_certificate = stage_file(
        prepared.certificate_path,
        combined_certificate.encode("ascii"),
        0o644,
    )
    try:
        staged_private_key = stage_file(
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
    certificate_target, private_key_target = credential_paths(
        config_path,
        certificate_path,
        private_key_path,
    )
    for path in (certificate_target, private_key_target):
        if path.exists() and not replace:
            raise CertificateEnrollmentError(
                f"Credential path already exists: {path}; use explicit rotation replacement"
            )

    private_key, csr, common_name = build_csr(profile)
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
                combined_certificate, expires_at = validate_issued_certificate(
                    certificate_pem=certificate_pem,
                    chain_pem=chain_pem,
                    private_key=private_key,
                    profile=profile,
                    expected_common_name=common_name,
                )
                commit_credential_set(
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
