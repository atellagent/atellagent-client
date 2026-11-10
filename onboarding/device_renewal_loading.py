# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Resolve bounded immutable renewal history; only the final leaf is current TLS."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import UnsupportedAlgorithm
from atellagent_client.sdk.private_keys import load_private_key

from .atomic_private_file import read_private_file
from .certificate_material import parse_certificates
from .contract_values import aware_time
from .device_certificate_verify import verify_device_material
from .device_renewal_preparation import public_key_der, private_key_pem
from .errors import OnboardingClientError


def _material(device, trust, *, history_only):
    key_raw = read_private_file(device.private_key_path, limit=8192)
    key = load_private_key(key_raw)
    if private_key_pem(key).encode() != key_raw:
        raise ValueError("canonical immutable device private key required")
    certificates = parse_certificates(read_private_file(device.certificate_path, limit=65536).decode("ascii"), maximum=9)
    pem = lambda cert: cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    verified = verify_device_material(tenant_id=device.tenant_id, device_id=device.device_id,
        public_key_der=public_key_der(key), certificate_pem=pem(certificates[0]),
        certificate_chain_pem="".join(pem(cert) for cert in certificates[1:]), trusted_ca_bundle_pem=trust.ca_bundle_pem,
        verification_time=certificates[0].not_valid_before_utc + timedelta(seconds=1)
            if history_only else datetime.now(timezone.utc))
    return verified, sha256(public_key_der(key)).hexdigest()


def load_selected_device_renewals(*, device, trust):
    """Local selection is history; resume still checks live signed device status."""
    return _load(device=device, trust=trust, history_only=False)


def load_device_renewal_history(*, device, trust):
    """Recovery input only. Even the last retained generation may be expired."""
    return _load(device=device, trust=trust, history_only=True)


def retained_device_certificate_metadata(*, device, trust):
    """Historical material metadata, not current authority."""
    try:
        verified, _ = _material(device, trust, history_only=True)
        return verified
    except (OSError, ValueError, TypeError, UnicodeError, UnsupportedAlgorithm) as exc:
        raise OnboardingClientError("device_renewal_history_unavailable") from exc


def read_device_renewal_generation(*, device, trust, record, history_only):
    parent, parent_key = _material(device, trust, history_only=True)
    fields = {"revision", "tenant_id", "device_id", "owner_user_id", "operation_id",
        "parent_certificate_sha256", "public_key_sha256", "certificate_sha256", "expires_at", "generation"}
    if (not isinstance(record, dict) or set(record) != fields or type(record["revision"]) is not int
            or record["revision"] != 1 or record["tenant_id"] != str(device.tenant_id)
            or record["device_id"] != str(device.device_id) or record["owner_user_id"] != str(device.owner_user_id)
            or record["parent_certificate_sha256"] != parent.certificate_sha256
            or not isinstance(record["operation_id"], str)
            or str(UUID(record["operation_id"])) != record["operation_id"]
            or not isinstance(record["generation"], str)
            or not re.fullmatch(r"device-[0-9a-f]{32}", record["generation"])):
        raise ValueError("exact retained device renewal history required")
    directory = device.certificate_path.parent / record["generation"]
    child = replace(device, certificate_path=directory / "certificate.pem", private_key_path=directory / "private-key.pem")
    verified, child_key = _material(child, trust, history_only=history_only)
    if (verified.certificate_sha256 != record["certificate_sha256"]
            or verified.expires_at != aware_time(record["expires_at"])
            or child_key != record["public_key_sha256"] or child_key == parent_key):
        raise ValueError("retained device material differs from immutable history")
    return child, verified


def _load(*, device, trust, history_only):
    try:
        current = device
        for _ in range(64):
            try:
                raw = read_private_file(current.certificate_path.parent / "device-renewed.json", limit=4096)
            except FileNotFoundError:
                _material(current, trust, history_only=history_only)
                return current
            record = json.loads(raw)
            child, _ = read_device_renewal_generation(device=current, trust=trust, record=record, history_only=True)
            pointer = current.certificate_path.parent / f"device-renewal-{record['operation_id']}.json"
            if read_private_file(pointer, limit=4096) != raw:
                raise ValueError("selection differs from retained candidate")
            current = child
        raise ValueError("device renewal history limit exceeded")
    except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError, UnsupportedAlgorithm) as exc:
        raise OnboardingClientError("device_renewal_history_unavailable") from exc
