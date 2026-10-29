"""Local certificate renewal preparation, independent of token onboarding."""

import os
import json
import re
from pathlib import Path
from uuid import UUID
from cryptography.hazmat.primitives import serialization
from .certificate_identity import ServiceAccountCertificateIdentity
from .certificate_rotation_types import PreparedCertificateRotation, StagedCertificateRotation
from .certificate_material import validate_issued_certificate, public_key_sha256
from .certificate_preparation import retained_certificate_preparation
from atellagent_client.onboarding.credential_generation import commit_credential_generation
from atellagent_client.onboarding.atomic_private_file import create_private_file, read_private_file
from .certificate_errors import CertificateMaterialError


def prepare_certificate_rotation(
    *,
    service_account_id: str,
    tenant_id: str,
    certificate_path: os.PathLike[str] | str,
    private_key_path: os.PathLike[str] | str,
) -> PreparedCertificateRotation:
    """Commit/reopen the exact replacement key and CSR before network issuance."""
    profile = ServiceAccountCertificateIdentity(
        service_account_id=str(UUID(str(service_account_id))),
        tenant_id=str(UUID(str(tenant_id))),
    )
    return retained_certificate_preparation(profile=profile,
        certificate_path=Path(certificate_path).expanduser(),
        private_key_path=Path(private_key_path).expanduser(),
    )


def stage_certificate_rotation(
    prepared: PreparedCertificateRotation,
    *,
    certificate_pem: str,
    certificate_chain_pem: str,
    trusted_ca_pem: str,
    operation_id: str,
) -> StagedCertificateRotation:
    """Validate and fsync replacement material before server-side activation."""
    combined_certificate, expires_at = validate_issued_certificate(
        certificate_pem=certificate_pem,
        chain_pem=certificate_chain_pem,
        private_key=prepared.private_key,
        profile=prepared.profile,
        expected_common_name=prepared.expected_common_name,
        trusted_ca_pem=trusted_ca_pem,
    )
    private_key_pem = prepared.private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    if prepared.certificate_path.parent != prepared.private_key_path.parent:
        raise CertificateMaterialError("Rotation requires a single managed credential generation")
    root = prepared.certificate_path.parent
    candidate = root / f"rotation-{UUID(operation_id)}.json"
    metadata = {"revision": 1, "operation_id": str(UUID(operation_id)),
        "service_account_id": prepared.profile.service_account_id,
        "tenant_id": prepared.profile.tenant_id, "expires_at": expires_at.isoformat(),
        "public_key_sha256": public_key_sha256(prepared.private_key.public_key())}
    try:
        commit_credential_generation(root, candidate, kind="integration", metadata=metadata,
            certificate=combined_certificate.encode("ascii"), private_key=private_key_pem)
    except FileExistsError:
        pass  # Revalidate the same durable candidate; never replace its key.
    record = json.loads(read_private_file(candidate, limit=4096))
    if ({name: value for name, value in record.items() if name != "generation"} != metadata
            or not isinstance(record.get("generation"), str)
            or not re.fullmatch(r"integration-[0-9a-f]{32}", record["generation"])):
        raise CertificateMaterialError("Rotation candidate identity changed")
    directory = root / record["generation"]
    if (read_private_file(directory / "certificate.pem", limit=65536) != combined_certificate.encode("ascii")
            or read_private_file(directory / "private-key.pem", limit=8192) != private_key_pem):
        raise CertificateMaterialError("Rotation candidate material changed")
    return StagedCertificateRotation(
        prepared=prepared,
        staged_certificate_path=directory / "certificate.pem",
        staged_private_key_path=directory / "private-key.pem",
        candidate_pointer=candidate,
        public_key_sha256=metadata["public_key_sha256"],
        certificate_expires_at=expires_at,
    )


def commit_staged_certificate_rotation(staged: StagedCertificateRotation) -> tuple[Path, Path]:
    """Select an immutable pair once, never overwrite the preceding generation."""
    try:
        record = read_private_file(staged.candidate_pointer, limit=4096)
        pointer = staged.prepared.certificate_path.parent / "renewed.json"
        try:
            create_private_file(pointer, record)
        except FileExistsError:
            if read_private_file(pointer, limit=4096) != record:
                raise ValueError("Another rotation is already selected")
        return staged.staged_certificate_path, staged.staged_private_key_path
    except Exception as exc:
        raise CertificateMaterialError(
            "Activated certificate rotation could not install staged credentials"
        ) from exc
