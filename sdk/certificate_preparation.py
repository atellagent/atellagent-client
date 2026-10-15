"""Persist a private replacement key and exact CSR before any issuance request."""

import json
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.x509.oid import NameOID
from atellagent_client.onboarding.atomic_private_file import create_private_file, read_private_file
from atellagent_client.onboarding.proofs import canonical_json
from atellagent_client.onboarding.service_account_csr import service_account_csr_facts
from .certificate_material import build_csr, public_key_sha256
from .certificate_rotation_types import PreparedCertificateRotation
from .certificate_errors import CertificateMaterialError
from .private_keys import load_private_key


def retained_certificate_preparation(*, profile, certificate_path, private_key_path):
    if certificate_path == private_key_path or certificate_path.parent != private_key_path.parent:
        raise CertificateMaterialError("Rotation requires a single managed credential generation")
    pointer = certificate_path.parent / "rotation-prepared.json"
    try:
        try:
            raw = read_private_file(pointer, limit=16384)
        except FileNotFoundError:
            key, csr, _ = build_csr(profile)
            record = dict(revision=1, service_account_id=profile.service_account_id, tenant_id=profile.tenant_id,
                certificate_name=certificate_path.name, private_key_name=private_key_path.name,
                csr_pem=csr.public_bytes(serialization.Encoding.PEM).decode("ascii"),
                private_key_pem=key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode("ascii"))
            try:
                create_private_file(pointer, canonical_json(record))
            except FileExistsError:
                pass  # Concurrent preparation adopts the committed candidate.
            raw = read_private_file(pointer, limit=16384)
        record = json.loads(raw)
        fields = {"revision", "service_account_id", "tenant_id", "certificate_name", "private_key_name", "csr_pem", "private_key_pem"}
        if (not isinstance(record, dict) or set(record) != fields or type(record["revision"]) is not int
                or record["revision"] != 1 or record["service_account_id"] != profile.service_account_id
                or record["tenant_id"] != profile.tenant_id or record["certificate_name"] != certificate_path.name
                or record["private_key_name"] != private_key_path.name):
            raise ValueError("exact retained preparation scope required")
        key = load_private_key(record["private_key_pem"].encode("ascii"))
        if (not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 2048
                or key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption()).decode("ascii") != record["private_key_pem"]):
            raise ValueError("canonical private RSA-2048 preparation required")
        facts = service_account_csr_facts(record["csr_pem"])
        csr = x509.load_pem_x509_csr(record["csr_pem"].encode("ascii"))
        if (str(facts.service_account_id), str(facts.tenant_id), facts.public_key_sha256) != (
                profile.service_account_id, profile.tenant_id, public_key_sha256(key.public_key())):
            raise ValueError("retained CSR differs from replacement key/identity")
        common_name = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        return PreparedCertificateRotation(profile, key, record["csr_pem"], common_name, certificate_path, private_key_path)
    except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError, UnsupportedAlgorithm,
            x509.ExtensionNotFound, x509.DuplicateExtension) as exc:
        raise CertificateMaterialError("Retained certificate rotation preparation is unavailable") from exc
