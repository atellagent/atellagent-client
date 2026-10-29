"""Reconcile retained replacement material before runtime startup, never reenroll."""

from dataclasses import replace
import json
import re

from cryptography.hazmat.primitives import serialization, hashes
from atellagent_client.sdk.private_keys import load_private_key
from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.x509.oid import NameOID
from .atomic_private_file import read_private_file
from .certificate_material import parse_certificates
from .contract_values import canonical_id
from .errors import OnboardingClientError
from .renewed_credential import load_retained_credential_history, load_renewed_credential, retained_rotation_paths
from .service_account_csr import service_account_csr
from atellagent_client.sdk.certificate_identity import ServiceAccountCertificateIdentity
from atellagent_client.sdk.certificate_rotation import stage_certificate_rotation, commit_staged_certificate_rotation
from atellagent_client.sdk.certificate_rotation_types import PreparedCertificateRotation
from atellagent_client.sdk.certificate_errors import CertificateMaterialError
from atellagent_client.connected.rotation_recovery import recover_activated_rotation, verify_pending_rotation
from atellagent_client.connected.participant_protocol import ConnectedHTTPError


def load_retained_rotation(stored, *, pointer, trust, tenant_id):
    try:
        record = json.loads(read_private_file(pointer, limit=4096))
        operation = canonical_id(record["operation_id"])
        if (pointer.parent != stored.certificate_path.parent or pointer.name != f"rotation-{operation}.json"
                or not isinstance(record.get("generation"), str)
                or not re.fullmatch(r"integration-[0-9a-f]{32}", record["generation"])):
            raise ValueError("exact retained candidate path required")
        directory = pointer.parent / record["generation"]
        key = load_private_key(read_private_file(directory / "private-key.pem", limit=8192))
        profile = ServiceAccountCertificateIdentity(service_account_id=str(stored.credential.service_account_id), tenant_id=str(tenant_id))
        csr = service_account_csr(key, tenant_id=tenant_id, service_account_id=stored.credential.service_account_id)
        name = x509.load_pem_x509_csr(csr.encode()).subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        prepared = PreparedCertificateRotation(profile, key, csr, name, stored.certificate_path, stored.private_key_path)
        material = parse_certificates(read_private_file(directory / "certificate.pem", limit=65536).decode("ascii"), maximum=9)
        pem = lambda cert: cert.public_bytes(serialization.Encoding.PEM).decode()
        staged = stage_certificate_rotation(prepared, certificate_pem=pem(material[0]),
            certificate_chain_pem="".join(pem(cert) for cert in material[1:]), trusted_ca_pem=trust.ca_bundle_pem,
            operation_id=str(operation))
        credential = replace(stored.credential, certificate_pem=pem(material[0]),
            certificate_chain_pem="".join(pem(cert) for cert in material[1:]),
            certificate_sha256=material[0].fingerprint(hashes.SHA256()).hex(),
            expires_at=staged.certificate_expires_at)
        return replace(stored, credential=credential, certificate_path=staged.staged_certificate_path,
                       private_key_path=staged.staged_private_key_path), staged
    except (OSError, ValueError, TypeError, KeyError, IndexError, CertificateMaterialError,
            UnsupportedAlgorithm, x509.ExtensionNotFound, x509.DuplicateExtension) as exc:
        raise OnboardingClientError("onboarding_rotation_candidate_invalid") from exc


async def reconcile_retained_rotation(stored, *, trust, tenant_id, make_config, device_proof_provider=None):
    history = load_retained_credential_history(stored, trust=trust, tenant_id=tenant_id)
    candidates = retained_rotation_paths(history.certificate_path.parent)
    if not candidates:
        return load_renewed_credential(stored, trust=trust, tenant_id=tenant_id)
    for pointer in candidates:
        candidate, staged = load_retained_rotation(history, pointer=pointer, trust=trust, tenant_id=tenant_id)
        try:
            config = await make_config(candidate)
            await recover_activated_rotation(config=config, staged=staged, device_proof_provider=device_proof_provider)
        except OnboardingClientError as exc:
            if exc.status not in {401, 403, 404}:
                raise
        except ConnectedHTTPError as exc:
            if exc.status_code not in {401, 403, 404}:
                raise  # Outages are not evidence of an inactive operation.
        else:
            commit_staged_certificate_rotation(staged)
            return load_renewed_credential(stored, trust=trust, tenant_id=tenant_id)
        # An inactive replacement is not permission to use stale authority.
        # Verify the old certificate now and its exact still-pending server op;
        # a restarted receiver can then acquire a fresh delivery lease.
        current = load_renewed_credential(stored, trust=trust, tenant_id=tenant_id)
        config = await make_config(current)
        await verify_pending_rotation(config=config, staged=staged, device_proof_provider=device_proof_provider)
        return current
    raise OnboardingClientError("onboarding_rotation_reconciliation_required")
