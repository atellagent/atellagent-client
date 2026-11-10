"""Real test certificates for the public device trust/issuance boundary."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

from atellagent_client.onboarding.device_csr import device_csr, device_certificate_operation_id
from atellagent_client.onboarding.device_trust import DeviceTrustBundle
from atellagent_client.onboarding.setup_draft import SetupDraft
from atellagent_client.onboarding.setup_keys import SetupKeys
from atellagent_client.onboarding.setup_receipt import SetupReceipt


def certificate_case(*, leaf_days=30, server_auth=False, ca_leaf=False, wrong_dns=False, expired=False, future=False,
                     include_authority=False, trusted_intermediate=False):
    draft = SetupDraft(uuid4(), uuid4(), uuid4(), "https://platform.example", "device",
        "My Mac", "0.9.1", "macos", SetupKeys.generate())
    now = datetime.now(timezone.utc)
    receipt = SetupReceipt(draft.operation_id, "device", "approved", uuid4(), 1, now, now)
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test root")])
    root = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(root_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365)).add_extension(x509.BasicConstraints(ca=True, path_length=1), True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), False)
        .sign(root_key, hashes.SHA256()))
    issuer, issuer_key = root, root_key
    if trusted_intermediate:
        issuer_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        issuer = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test intermediate")]))
            .issuer_name(root.subject).public_key(issuer_key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=180))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(issuer_key.public_key()), False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), False)
            .sign(root_key, hashes.SHA256()))
    csr = x509.load_pem_x509_csr(device_csr(draft, receipt).encode())
    builder = (x509.CertificateBuilder().subject_name(csr.subject).issuer_name(issuer.subject)
        .public_key(csr.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now + timedelta(days=1) if future else now - timedelta(days=1) if expired else now - timedelta(minutes=1))
        .not_valid_after(now - timedelta(hours=1) if expired else now + timedelta(days=leaf_days)))
    for extension in csr.extensions:
        value = extension.value
        if server_auth and isinstance(value, x509.ExtendedKeyUsage):
            value = x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH])
        if ca_leaf and isinstance(value, x509.BasicConstraints):
            value = x509.BasicConstraints(ca=True, path_length=None)
        if wrong_dns and isinstance(value, x509.SubjectAlternativeName):
            value = x509.SubjectAlternativeName([x509.DNSName("unrelated.identity.invalid")])
        builder = builder.add_extension(value, extension.critical)
    leaf = (builder.add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), False)
        .sign(issuer_key, hashes.SHA256()))
    root_pem = root.public_bytes(serialization.Encoding.PEM).decode()
    if trusted_intermediate:
        root_pem = issuer.public_bytes(serialization.Encoding.PEM).decode() + root_pem
    trust = DeviceTrustBundle(draft.platform_origin, root_pem, sha256(root_pem.encode()).hexdigest(),
        "https://mtls.platform.example:8443")
    payload = {"operation_id": str(device_certificate_operation_id(draft.operation_id)),
        "status": "awaiting_activation", "device_id": str(receipt.device_id),
        "certificate_pem": leaf.public_bytes(serialization.Encoding.PEM).decode(),
        "certificate_chain_pem": root_pem,
        "certificate_sha256": sha256(leaf.public_bytes(serialization.Encoding.DER)).hexdigest(),
        "expires_at": leaf.not_valid_after_utc.isoformat()}
    result = draft, receipt, trust, payload
    return (*result, (root, root_key)) if include_authority else result
