"""Real locally generated renewal material remains independent of token setup."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from atellagent_client.sdk.certificate_rotation import (
    prepare_certificate_rotation, stage_certificate_rotation,
    commit_staged_certificate_rotation,
)
from atellagent_client.sdk.certificate_errors import CertificateMaterialError
from atellagent_client.onboarding.atomic_private_file import create_private_file


def issued(prepared, *, wrong_key=False, defect=None):
    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test issuer")])
    root = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now - timedelta(seconds=1) if defect == "expired_issuer" else now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .sign(key, hashes.SHA256()))
    csr = x509.load_pem_x509_csr(prepared.csr_pem.encode())
    public_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key() if wrong_key else csr.public_key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "wrong subject")]) if defect == "subject" else csr.subject
    start = now - timedelta(days=2) if defect == "expired" else now - timedelta(minutes=1)
    end = now - timedelta(days=1) if defect == "expired" else now + timedelta(days=1)
    leaf = (x509.CertificateBuilder().subject_name(subject).issuer_name(name).public_key(public_key)
        .serial_number(x509.random_serial_number()).not_valid_before(start)
        .not_valid_after(end))
    for extension in csr.extensions:
        value = extension.value
        if defect == "san" and isinstance(value, x509.SubjectAlternativeName):
            value = x509.SubjectAlternativeName([x509.DNSName("wrong.example")])
        if defect == "ca" and isinstance(value, x509.BasicConstraints):
            value = x509.BasicConstraints(ca=True, path_length=None)
        leaf = leaf.add_extension(value, extension.critical)
    leaf = (leaf.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), False)
        .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), True)
        .sign(key, hashes.SHA256()))
    return dict(certificate_pem=leaf.public_bytes(serialization.Encoding.PEM).decode(),
                operation_id=str(uuid4()),
                certificate_chain_pem=root.public_bytes(serialization.Encoding.PEM).decode(),
                trusted_ca_pem=root.public_bytes(serialization.Encoding.PEM).decode())


def prepare(tmp_path):
    return prepare_certificate_rotation(service_account_id=str(uuid4()), tenant_id=str(uuid4()),
        certificate_path=tmp_path / "client.pem", private_key_path=tmp_path / "key.pem")


def test_rotation_identity_and_csr_do_not_require_enrollment_endpoint(tmp_path):
    prepared = prepare(tmp_path)
    assert not hasattr(prepared.profile, "enrollment_url")
    csr = x509.load_pem_x509_csr(prepared.csr_pem.encode())
    assert csr.is_signature_valid
    assert csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
        x509.DNSName) == [prepared.profile.subject_dns]
    staged = stage_certificate_rotation(prepared, **issued(prepared))
    assert not prepared.certificate_path.exists() and not prepared.private_key_path.exists()
    certificate_path, private_key_path = commit_staged_certificate_rotation(staged)
    assert not prepared.certificate_path.exists() and not prepared.private_key_path.exists()
    leaf = x509.load_pem_x509_certificate(certificate_path.read_bytes())
    private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    assert leaf.public_key().public_numbers() == private_key.public_key().public_numbers()


def test_wrong_issued_key_is_rejected_without_installing_files(tmp_path):
    prepared = prepare(tmp_path)
    with pytest.raises(CertificateMaterialError):
        stage_certificate_rotation(prepared, **issued(prepared, wrong_key=True))
    assert [path.name for path in tmp_path.iterdir()] == ["rotation-prepared.json"]


def test_ambiguous_failure_retains_recoverable_unselected_material(tmp_path):
    prepared = prepare(tmp_path)
    staged = stage_certificate_rotation(prepared, **issued(prepared))
    assert staged.staged_certificate_path.exists() and staged.staged_private_key_path.exists()
    assert staged.candidate_pointer.exists() and not (tmp_path / "renewed.json").exists()


def test_response_cannot_establish_its_own_trust_root(tmp_path):
    prepared = prepare(tmp_path)
    material = issued(prepared)
    material["trusted_ca_pem"] = issued(prepared)["trusted_ca_pem"]
    with pytest.raises(CertificateMaterialError, match="independently trusted"):
        stage_certificate_rotation(prepared, **material)
    assert [path.name for path in tmp_path.iterdir()] == ["rotation-prepared.json"]


def test_selection_failure_preserves_old_pair_and_candidate_for_retry(tmp_path):
    prepared = prepare(tmp_path)
    create_private_file(prepared.certificate_path, b"old certificate")
    create_private_file(prepared.private_key_path, b"old key")
    material = issued(prepared)
    staged = stage_certificate_rotation(prepared, **material)
    retry = stage_certificate_rotation(prepared, **material)
    assert retry.staged_certificate_path == staged.staged_certificate_path
    with patch("atellagent_client.sdk.certificate_rotation.create_private_file", side_effect=OSError("interrupted")):
        with pytest.raises(CertificateMaterialError):
            commit_staged_certificate_rotation(staged)
    assert prepared.certificate_path.read_bytes() == b"old certificate"
    assert prepared.private_key_path.read_bytes() == b"old key"
    assert not (tmp_path / "renewed.json").exists()
    selected = commit_staged_certificate_rotation(staged)
    assert commit_staged_certificate_rotation(staged) == selected
    assert prepared.certificate_path.read_bytes() == b"old certificate"
    assert prepared.private_key_path.read_bytes() == b"old key"


def test_conflicting_rotation_cannot_replace_selected_generation(tmp_path):
    first = prepare(tmp_path)
    staged = stage_certificate_rotation(first, **issued(first))
    selected = commit_staged_certificate_rotation(staged)
    competing = stage_certificate_rotation(first, **issued(first))
    with pytest.raises(CertificateMaterialError):
        commit_staged_certificate_rotation(competing)
    assert commit_staged_certificate_rotation(staged) == selected


@pytest.mark.parametrize("defect", ["subject", "san", "ca", "expired", "expired_issuer", "chain", "pem"])
def test_invalid_material_is_rejected_before_staging(tmp_path, defect):
    prepared = prepare(tmp_path)
    material = issued(prepared, defect=defect)
    if defect == "chain":
        material["certificate_chain_pem"] = "not a certificate"
    if defect == "pem":
        material["certificate_pem"] = "not a certificate"
    with pytest.raises(CertificateMaterialError):
        stage_certificate_rotation(prepared, **material)
    assert [path.name for path in tmp_path.iterdir()] == ["rotation-prepared.json"]
