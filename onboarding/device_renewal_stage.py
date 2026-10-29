# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Immutable replacement material and receipt-bound selection, never in-place keys."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from .atomic_private_file import create_private_file, read_private_file
from .contract_values import aware_time
from .credential_generation import commit_credential_generation
from .device_certificate_verify import VerifiedDeviceCredential, verify_device_material
from .device_renewal_preparation import PreparedDeviceRenewal, private_key_pem, public_key_der, prepare_device_renewal
from .errors import OnboardingClientError


@dataclass(frozen=True)
class StagedDeviceRenewal:
    prepared: PreparedDeviceRenewal
    credential: VerifiedDeviceCredential
    pointer: Path
    certificate_path: Path
    private_key_path: Path
    record: bytes = field(repr=False)


def stage_device_renewal(*, device, trust, prepared, payload) -> StagedDeviceRenewal:
    try:
        retained = prepare_device_renewal(device=device, trust=trust)
        if (retained.operation_id != prepared.operation_id
                or retained.parent_certificate_sha256 != prepared.parent_certificate_sha256
                or retained.public_key_sha256 != prepared.public_key_sha256
                or retained.csr_pem != prepared.csr_pem):
            raise ValueError("exact retained device preparation required")
        fields = {"operation_id", "status", "device_id", "public_key_sha256", "deadline_at",
            "certificate_pem", "certificate_chain_pem", "certificate_sha256", "expires_at"}
        if (not isinstance(payload, dict) or set(payload) != fields or payload["status"] != "issued"
                or payload["operation_id"] != str(prepared.operation_id)
                or payload["device_id"] != str(device.device_id)
                or payload["public_key_sha256"] != prepared.public_key_sha256
                or aware_time(payload["deadline_at"]) <= datetime.now(timezone.utc)):
            raise ValueError("exact live issued renewal required")
        verified = verify_device_material(tenant_id=device.tenant_id, device_id=device.device_id,
            public_key_der=public_key_der(prepared.private_key), certificate_pem=payload["certificate_pem"],
            certificate_chain_pem=payload["certificate_chain_pem"], trusted_ca_bundle_pem=trust.ca_bundle_pem)
        if (verified.certificate_sha256 != payload["certificate_sha256"]
                or verified.expires_at != aware_time(payload["expires_at"])):
            raise ValueError("exact verified renewal metadata required")
        root = device.certificate_path.parent
        pointer = root / f"device-renewal-{prepared.operation_id}.json"
        metadata = dict(revision=1, tenant_id=str(device.tenant_id), device_id=str(device.device_id),
            owner_user_id=str(device.owner_user_id), operation_id=str(prepared.operation_id),
            parent_certificate_sha256=prepared.parent_certificate_sha256,
            public_key_sha256=prepared.public_key_sha256, certificate_sha256=verified.certificate_sha256,
            expires_at=verified.expires_at.isoformat())
        certificate = (verified.certificate_pem.strip() + "\n" + verified.certificate_chain_pem.strip() + "\n").encode()
        key = private_key_pem(prepared.private_key).encode()
        try:
            raw = read_private_file(pointer, limit=4096)
        except FileNotFoundError:
            try:
                commit_credential_generation(root, pointer, kind="device", metadata=metadata,
                    certificate=certificate, private_key=key)
            except FileExistsError:
                pass
            raw = read_private_file(pointer, limit=4096)
        record = json.loads(raw)
        if (not isinstance(record, dict) or set(record) != set(metadata) | {"generation"}
                or type(record["revision"]) is not int
                or any(record[name] != value for name, value in metadata.items())
                or not isinstance(record["generation"], str)
                or not re.fullmatch(r"device-[0-9a-f]{32}", record["generation"])):
            raise ValueError("exact immutable device renewal generation required")
        certificate_path, key_path = root / record["generation"] / "certificate.pem", root / record["generation"] / "private-key.pem"
        if (read_private_file(certificate_path, limit=65536) != certificate
                or read_private_file(key_path, limit=8192) != key):
            raise ValueError("retained replacement differs from verified material")
        return StagedDeviceRenewal(prepared, verified, pointer, certificate_path, key_path, raw)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise OnboardingClientError("device_renewal_material_unavailable") from exc


def select_device_renewal(*, staged: StagedDeviceRenewal, receipt: dict) -> None:
    """Caller obtained this receipt over replacement mTLS with device proof."""
    try:
        if (not isinstance(receipt, dict) or set(receipt) != {"operation_id", "status", "device_id",
                "certificate_sha256", "public_key_sha256", "expires_at"}
                or receipt["operation_id"] != str(staged.prepared.operation_id)
                or receipt["status"] != "activated" or receipt["device_id"] != str(staged.credential.device_id)
                or receipt["certificate_sha256"] != staged.credential.certificate_sha256
                or receipt["public_key_sha256"] != staged.prepared.public_key_sha256
                or aware_time(receipt["expires_at"]) != staged.credential.expires_at):
            raise ValueError("exact activated replacement receipt required")
        raw = read_private_file(staged.pointer, limit=4096)
        expected_certificate = (staged.credential.certificate_pem.strip() + "\n"
            + staged.credential.certificate_chain_pem.strip() + "\n").encode()
        if (raw != staged.record or staged.credential.expires_at <= datetime.now(timezone.utc)
                or read_private_file(staged.certificate_path, limit=65536) != expected_certificate
                or read_private_file(staged.private_key_path, limit=8192) != private_key_pem(staged.prepared.private_key).encode()):
            raise ValueError("exact valid immutable staged material required")
        selection = staged.pointer.parent / "device-renewed.json"
        try:
            create_private_file(selection, raw)
        except FileExistsError:
            if read_private_file(selection, limit=4096) != raw:
                raise ValueError("another device renewal is selected")
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise OnboardingClientError("device_renewal_selection_unavailable") from exc
