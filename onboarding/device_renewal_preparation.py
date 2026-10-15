# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Retain one exact device replacement proposal before contacting the issuer."""

from dataclasses import dataclass, field
from datetime import timedelta
from hashlib import sha256
import json
from uuid import UUID, uuid4

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from atellagent_client.sdk.private_keys import load_private_key

from .atomic_private_file import create_private_file, read_private_file
from .certificate_material import parse_certificates
from .device_certificate_verify import verify_device_material
from .device_csr import build_device_csr
from .errors import OnboardingClientError
from .proofs import canonical_json


@dataclass(frozen=True)
class PreparedDeviceRenewal:
    operation_id: UUID
    parent_certificate_sha256: str
    public_key_sha256: str
    csr_pem: str
    private_key: rsa.RSAPrivateKey = field(repr=False)


def private_key_pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode("ascii")


def public_key_der(key):
    return key.public_key().public_bytes(serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)


def prepare_device_renewal(*, device, trust) -> PreparedDeviceRenewal:
    """Only current independently verified material can initiate renewal.

    Retention is not authorization. Gateway still checks the current parent,
    membership, device status and exact request proof on each operation.
    """
    return _prepare(device=device, trust=trust, history_only=False)


def load_retained_device_renewal_preparation(*, device, trust) -> PreparedDeviceRenewal:
    """Read existing recovery material only; never initiate issuance or create keys."""
    return _prepare(device=device, trust=trust, history_only=True)


def _prepare(*, device, trust, history_only):
    try:
        if (device.certificate_path == device.private_key_path
                or device.certificate_path.parent != device.private_key_path.parent):
            raise ValueError("single managed device generation required")
        parent_key = load_private_key(read_private_file(device.private_key_path, limit=8192))
        parent = parse_certificates(read_private_file(device.certificate_path, limit=65536).decode("ascii"), maximum=9)
        pem = lambda cert: cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
        current = verify_device_material(tenant_id=device.tenant_id, device_id=device.device_id,
            public_key_der=public_key_der(parent_key), certificate_pem=pem(parent[0]),
            certificate_chain_pem="".join(pem(cert) for cert in parent[1:]), trusted_ca_bundle_pem=trust.ca_bundle_pem,
            verification_time=parent[0].not_valid_before_utc + timedelta(seconds=1) if history_only else None)
        pointer = device.certificate_path.parent / "device-renewal-prepared.json"
        scope = dict(revision=1, tenant_id=str(device.tenant_id), device_id=str(device.device_id),
            owner_user_id=str(device.owner_user_id), parent_certificate_sha256=current.certificate_sha256,
            certificate_name=device.certificate_path.name, private_key_name=device.private_key_path.name)
        try:
            raw = read_private_file(pointer, limit=16384)
        except FileNotFoundError:
            if history_only:
                raise OnboardingClientError("device_renewal_preparation_missing")
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            record = {**scope, "operation_id": str(uuid4()), "private_key_pem": private_key_pem(key),
                "csr_pem": build_device_csr(tenant_id=device.tenant_id, device_id=device.device_id, tls_key=key)}
            try:
                create_private_file(pointer, canonical_json(record))
            except FileExistsError:
                pass  # Adopt and validate the concurrently committed proposal.
            raw = read_private_file(pointer, limit=16384)
        record = json.loads(raw)
        if (not isinstance(record, dict) or set(record) != set(scope) | {"operation_id", "private_key_pem", "csr_pem"}
                or type(record["revision"]) is not int or any(record[name] != value for name, value in scope.items())
                or any(not isinstance(record[name], str) for name in ("operation_id", "private_key_pem", "csr_pem"))):
            raise ValueError("exact retained device renewal scope required")
        operation_id = UUID(record["operation_id"])
        key = load_private_key(record["private_key_pem"].encode("ascii"))
        if (str(operation_id) != record["operation_id"] or not isinstance(key, rsa.RSAPrivateKey)
                or key.key_size != 2048 or private_key_pem(key) != record["private_key_pem"]
                or public_key_der(key) == public_key_der(parent_key)
                or build_device_csr(tenant_id=device.tenant_id, device_id=device.device_id, tls_key=key) != record["csr_pem"]):
            raise ValueError("exact canonical replacement key and CSR required")
        return PreparedDeviceRenewal(operation_id, current.certificate_sha256,
            sha256(public_key_der(key)).hexdigest(), record["csr_pem"], key)
    except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError, UnsupportedAlgorithm) as exc:
        raise OnboardingClientError("device_renewal_preparation_unavailable") from exc
