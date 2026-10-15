"""Resolve immutable renewal selections; history is not live execution authority."""

from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json
import re

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from atellagent_client.sdk.private_keys import load_private_key

from .atomic_private_file import read_private_file
from .certificate_material import parse_certificates, verify_client_chain
from cryptography.x509.verification import VerificationError
from .contract_values import canonical_id, aware_time
from .errors import OnboardingClientError
from .service_account_certificate_verify import verify_service_account_delivery
from .service_account_csr import service_account_csr


def retained_history_time(certificate_path, leaf):
    """An expired predecessor may be verified only when a renewal was selected.

    The resolver separately verifies the final selected leaf at current time.
    This permits archival identity validation, never expired runtime credentials.
    """
    try:
        read_private_file(certificate_path.parent / "renewed.json", limit=4096)
    except FileNotFoundError:
        if not retained_rotation_paths(certificate_path.parent):
            return None
    return leaf.not_valid_before_utc + timedelta(seconds=1)


def load_renewed_credential(stored, *, trust, tenant_id):
    return _resolve_renewals(stored, trust=trust, tenant_id=tenant_id, history_only=False)


def load_retained_credential_history(stored, *, trust, tenant_id):
    """Recovery input only; this must never authorize TLS or agent execution."""
    return _resolve_renewals(stored, trust=trust, tenant_id=tenant_id, history_only=True)


def retained_rotation_paths(root):
    paths = sorted(root.glob("rotation-????????-????-????-????-????????????.json"))
    if len(paths) > 8:
        raise OnboardingClientError("onboarding_rotation_recovery_limit")
    for path in paths:
        canonical_id(path.name.removeprefix("rotation-").removesuffix(".json"))
        read_private_file(path, limit=4096)
    return paths


def _resolve_renewals(stored, *, trust, tenant_id, history_only):
    initial = stored.credential
    current = stored
    try:
        trust.validated(expected_origin=trust.platform_origin)
        for _ in range(64):
            root = current.certificate_path.parent
            try:
                record = json.loads(read_private_file(root / "renewed.json", limit=4096))
            except FileNotFoundError:
                leaf = parse_certificates(current.credential.certificate_pem, maximum=1)[0]
                verify_client_chain(leaf, current.credential.certificate_chain_pem, trust.ca_bundle_pem,
                    verification_time=retained_history_time(current.certificate_path, leaf) if history_only else None)
                return current
            if (not isinstance(record, dict) or set(record) != {"revision", "operation_id",
                    "service_account_id", "tenant_id", "expires_at", "public_key_sha256", "generation"}
                    or type(record["revision"]) is not int or record["revision"] != 1
                    or record["service_account_id"] != str(initial.service_account_id)
                    or record["tenant_id"] != str(tenant_id)
                    or not isinstance(record["generation"], str)
                    or not re.fullmatch(r"integration-[0-9a-f]{32}", record["generation"])):
                raise ValueError("exact selected renewal identity required")
            canonical_id(record["operation_id"])
            directory = root / record["generation"]
            certificate, private_key = directory / "certificate.pem", directory / "private-key.pem"
            material = parse_certificates(read_private_file(certificate, limit=65536).decode("ascii"), maximum=9)
            key = load_private_key(read_private_file(private_key, limit=8192))
            public_der = key.public_key().public_bytes(serialization.Encoding.DER,
                                                      serialization.PublicFormat.SubjectPublicKeyInfo)
            if sha256(public_der).hexdigest() != record["public_key_sha256"]:
                raise ValueError("selected renewal key differs from record")
            pem = lambda cert: cert.public_bytes(serialization.Encoding.PEM).decode()
            payload = {"operation_id": record["operation_id"], "status": "awaiting_activation",
                "service_account_id": str(initial.service_account_id), "integration_id": str(initial.integration_id),
                "certificate_pem": pem(material[0]), "certificate_chain_pem": "".join(pem(cert) for cert in material[1:]),
                "certificate_sha256": sha256(material[0].public_bytes(serialization.Encoding.DER)).hexdigest(),
                "expires_at": record["expires_at"]}
            aware_time(record["expires_at"])
            verified = verify_service_account_delivery(platform_origin=trust.platform_origin,
                tenant_id=tenant_id, service_account_id=initial.service_account_id,
                integration_id=initial.integration_id, csr_pem=service_account_csr(key,
                    tenant_id=tenant_id, service_account_id=initial.service_account_id),
                approved_public_key_der=public_der, trust=trust, payload=payload,
                verification_time=retained_history_time(certificate, material[0]))
            # Preserve setup/activation history identity; renewal operation is a
            # separate record, not a new enrollment or user delegation.
            current = replace(stored, credential=replace(verified, operation_id=initial.operation_id),
                              certificate_path=certificate, private_key_path=private_key)
        raise ValueError("renewal history exceeds bounded local recovery depth")
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, x509.ExtensionNotFound, VerificationError) as exc:
        raise OnboardingClientError("onboarding_renewed_credential_invalid") from exc
