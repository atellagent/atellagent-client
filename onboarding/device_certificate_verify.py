# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Issued results cannot supply their own trust anchors or replace retained keys."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.x509.verification import VerificationError

from .certificate_material import parse_certificates, verify_client_chain
from .contract_values import aware_time
from .device_csr import device_certificate_operation_id
from .device_leaf import validate_device_leaf_identity
from .device_trust import DeviceTrustBundle
from .errors import OnboardingClientError


@dataclass(frozen=True)
class VerifiedDeviceCredential:
    device_id: UUID
    certificate_pem: str
    certificate_chain_pem: str
    certificate_sha256: str
    expires_at: datetime


def verify_device_material(*, tenant_id: UUID, device_id: UUID, public_key_der: bytes,
                           certificate_pem: str, certificate_chain_pem: str,
                           trusted_ca_bundle_pem: str, verification_time=None) -> VerifiedDeviceCredential:
    """Identity/trust verification shared by admission, renewal and retained history.

    This checks material only. It does not authorize issuance or activation.
    Trust must come from independently verified platform metadata.
    An explicit historical clock validates archival material only; current TLS
    paths must use current time and still require live server authority.
    """
    try:
        leaf = parse_certificates(certificate_pem, maximum=1)[0]
        validate_device_leaf_identity(leaf, tenant_id=tenant_id, device_id=device_id,
            public_key_der=public_key_der)
        verify_client_chain(leaf, certificate_chain_pem, trusted_ca_bundle_pem, verification_time=verification_time)
        fingerprint = sha256(leaf.public_bytes(serialization.Encoding.DER)).hexdigest()
        return VerifiedDeviceCredential(device_id, certificate_pem, certificate_chain_pem,
            fingerprint, leaf.not_valid_after_utc)
    except (ValueError, TypeError, KeyError, UnicodeError, UnsupportedAlgorithm,
        x509.ExtensionNotFound, x509.DuplicateExtension, VerificationError) as exc:
        raise OnboardingClientError("onboarding_device_certificate_invalid") from exc


def verify_device_delivery(*, draft, receipt, trust: DeviceTrustBundle, payload: dict,
                           verification_time=None) -> VerifiedDeviceCredential:
    try:
        checked = DeviceTrustBundle.from_payload({"platform_origin": trust.platform_origin,
            "device_api_origin": trust.device_api_origin,
            "protocol_revision": 1, "certificate_profile": "atellagent-device-client-v1",
            "ca_bundle_pem": trust.ca_bundle_pem, "ca_bundle_sha256": trust.ca_bundle_sha256},
            expected_origin=draft.platform_origin)
        if (set(payload) != {"operation_id", "status", "device_id", "certificate_pem", "certificate_chain_pem", "certificate_sha256", "expires_at"}
            or payload["operation_id"] != str(device_certificate_operation_id(draft.operation_id))
            or payload["device_id"] != str(receipt.device_id)
            or payload["status"] not in {"awaiting_activation", "device_ready"}):
            raise ValueError("exact issued device result required")
        if (draft.kind != "device" or receipt.kind != "device"
                or receipt.id != draft.operation_id or receipt.device_id is None):
            raise ValueError("exact device assignment required")
        verified = verify_device_material(tenant_id=draft.tenant_id, device_id=receipt.device_id,
            public_key_der=draft.keys.tls_key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo),
            certificate_pem=payload["certificate_pem"], certificate_chain_pem=payload["certificate_chain_pem"],
            trusted_ca_bundle_pem=checked.ca_bundle_pem, verification_time=verification_time)
        if payload["certificate_sha256"] != verified.certificate_sha256 or aware_time(payload["expires_at"]) != verified.expires_at:
            raise ValueError("device certificate metadata differs from verified leaf")
        return verified
    except (ValueError, TypeError, KeyError, UnicodeError, UnsupportedAlgorithm,
        x509.ExtensionNotFound, x509.DuplicateExtension, VerificationError) as exc:
        raise OnboardingClientError("onboarding_device_certificate_invalid") from exc
