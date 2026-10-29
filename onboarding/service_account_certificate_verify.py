# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Service-account delivery cannot supply its own trust or replace the retained key."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID
from cryptography.x509.verification import VerificationError

from .certificate_material import parse_certificates, verify_client_chain
from .contract_values import aware_time, canonical_id
from .errors import OnboardingClientError
from .service_account_csr import service_account_csr_facts
from .service_account_trust import ServiceAccountTrustBundle


@dataclass(frozen=True)
class VerifiedServiceAccountCredential:
    operation_id: UUID
    service_account_id: UUID
    integration_id: UUID
    certificate_pem: str
    certificate_chain_pem: str
    certificate_sha256: str
    expires_at: datetime


def verify_service_account_delivery(*, platform_origin, tenant_id, service_account_id,
        integration_id, csr_pem, approved_public_key_der: bytes, trust: ServiceAccountTrustBundle,
        payload, verification_time=None) -> VerifiedServiceAccountCredential:
    try:
        trust.validated(expected_origin=platform_origin)
        if (not isinstance(payload, dict) or set(payload) != {"operation_id", "status", "service_account_id",
                "integration_id", "certificate_pem", "certificate_chain_pem", "certificate_sha256", "expires_at"}
                or payload["status"] != "awaiting_activation"
                or payload["service_account_id"] != str(service_account_id)
                or payload["integration_id"] != str(integration_id)):
            raise ValueError("exact service-account delivery required")
        operation = canonical_id(payload["operation_id"])
        facts = service_account_csr_facts(csr_pem)
        if facts.tenant_id != tenant_id or facts.service_account_id != service_account_id:
            raise ValueError("exact retained CSR identity required")
        csr = x509.load_pem_x509_csr(csr_pem.encode("ascii"))
        leaf = parse_certificates(payload["certificate_pem"], maximum=1)[0]
        key = leaf.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if (key != approved_public_key_der or leaf.subject != csr.subject
                or key != csr.public_key().public_bytes(serialization.Encoding.DER,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo)):
            raise ValueError("issued identity/key differs from approved retained CSR")
        dns = f"sa.{service_account_id.hex}.{tenant_id.hex}.identity.invalid"
        if list(leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value) != [x509.DNSName(dns)]:
            raise ValueError("exact service-account DNS required")
        constraints = leaf.extensions.get_extension_for_class(x509.BasicConstraints).value
        if (list(leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) != [ExtendedKeyUsageOID.CLIENT_AUTH]
                or constraints.ca or constraints.path_length is not None
                or leaf.not_valid_after_utc - leaf.not_valid_before_utc > timedelta(days=90, minutes=20)):
            raise ValueError("bounded client-only service-account leaf required")
        verify_client_chain(leaf, payload["certificate_chain_pem"], trust.ca_bundle_pem,
                            verification_time=verification_time)
        fingerprint = sha256(leaf.public_bytes(serialization.Encoding.DER)).hexdigest()
        if fingerprint != payload["certificate_sha256"] or aware_time(payload["expires_at"]) != leaf.not_valid_after_utc:
            raise ValueError("certificate metadata differs from verified leaf")
        # Compare verified certificate material in its canonical representation,
        # not the delivery envelope's optional surrounding PEM whitespace.
        leaf_pem = leaf.public_bytes(serialization.Encoding.PEM).decode("ascii")
        chain_pem = "".join(cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
                            for cert in parse_certificates(payload["certificate_chain_pem"], maximum=8))
        return VerifiedServiceAccountCredential(operation, service_account_id, integration_id,
            leaf_pem, chain_pem, fingerprint, leaf.not_valid_after_utc)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, UnsupportedAlgorithm,
            x509.ExtensionNotFound, x509.DuplicateExtension, VerificationError) as exc:
        raise OnboardingClientError("onboarding_service_account_certificate_invalid") from exc
