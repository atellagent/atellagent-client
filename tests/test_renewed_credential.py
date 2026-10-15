"""A selected current pair survives expiry of retained enrollment history."""

from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from atellagent_client.onboarding.atomic_private_file import create_private_file
from atellagent_client.onboarding.native_credential_record import StoredNativeCredential
from atellagent_client.onboarding.renewed_credential import load_renewed_credential
from atellagent_client.onboarding.service_account_certificate_verify import verify_service_account_delivery
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.sdk.certificate_rotation import prepare_certificate_rotation, stage_certificate_rotation, commit_staged_certificate_rotation
from service_account_certificate_support import certificate_case


def renewed_case(tmp_path, *, expired_initial=True):
    case = certificate_case(expired=expired_initial)
    credential = verify_service_account_delivery(**case.args,
        verification_time=case.leaf.not_valid_before_utc + timedelta(seconds=1) if expired_initial else None)
    certificate, key = tmp_path / "certificate.pem", tmp_path / "private-key.pem"
    create_private_file(certificate, (credential.certificate_pem + credential.certificate_chain_pem).encode())
    create_private_file(key, case.key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    stored = StoredNativeCredential(credential, certificate, key)
    prepared = prepare_certificate_rotation(service_account_id=str(credential.service_account_id),
        tenant_id=str(case.args["tenant_id"]), certificate_path=certificate, private_key_path=key)
    csr = x509.load_pem_x509_csr(prepared.csr_pem.encode())
    now = datetime.now(timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(csr.subject).issuer_name(case.root.subject)
        .public_key(csr.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=30)))
    for extension in csr.extensions:
        builder = builder.add_extension(extension.value, extension.critical)
    leaf = (builder.add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), True)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(case.root_key.public_key()), False)
        .sign(case.root_key, hashes.SHA256()))
    staged = stage_certificate_rotation(prepared, certificate_pem=leaf.public_bytes(serialization.Encoding.PEM).decode(),
        certificate_chain_pem=credential.certificate_chain_pem, trusted_ca_pem=case.args["trust"].ca_bundle_pem,
        operation_id=str(uuid4()))
    return case, stored, staged


def test_current_pair_loads_after_initial_certificate_expiry(tmp_path):
    case, stored, staged = renewed_case(tmp_path)
    paths = commit_staged_certificate_rotation(staged)
    loaded = load_renewed_credential(stored, trust=case.args["trust"], tenant_id=case.args["tenant_id"])
    assert (loaded.certificate_path, loaded.private_key_path) == paths
    assert loaded.credential.operation_id == stored.credential.operation_id
    assert loaded.credential.expires_at > datetime.now(timezone.utc)
    assert loaded.credential.certificate_sha256 != stored.credential.certificate_sha256


def test_unselected_candidate_does_not_revive_expired_initial_pair(tmp_path):
    case, stored, _ = renewed_case(tmp_path)
    with pytest.raises(OnboardingClientError):
        load_renewed_credential(stored, trust=case.args["trust"], tenant_id=case.args["tenant_id"])


@pytest.mark.parametrize("change", ["tenant", "account", "path", "key"])
def test_changed_renewal_selection_is_rejected(tmp_path, change):
    case, stored, staged = renewed_case(tmp_path)
    commit_staged_certificate_rotation(staged)
    pointer = tmp_path / "renewed.json"
    record = json.loads(pointer.read_bytes())
    if change == "tenant": record["tenant_id"] = str(uuid4())
    elif change == "account": record["service_account_id"] = str(uuid4())
    elif change == "path": record["generation"] = "../outside"
    else: record["public_key_sha256"] = "f" * 64
    pointer.write_text(json.dumps(record))
    with pytest.raises(OnboardingClientError):
        load_renewed_credential(stored, trust=case.args["trust"], tenant_id=case.args["tenant_id"])
