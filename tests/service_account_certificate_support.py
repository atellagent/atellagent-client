"""Real CA/leaf fixtures for exact retained service-account CSR verification."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

from atellagent_client.onboarding.service_account_csr import service_account_csr
from atellagent_client.onboarding.service_account_trust import ServiceAccountTrustBundle


def certificate_case(*, server_auth=False, ca_leaf=False, wrong_dns=False, expired=False, future=False, days=30):
    tenant, account, integration = uuid4(), uuid4(), uuid4()
    key, root_key = (rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2))
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Independent SA root")])
    root = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(root_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365)).add_extension(x509.BasicConstraints(ca=True, path_length=1), True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), False)
        .sign(root_key, hashes.SHA256()))
    csr_pem = service_account_csr(key, tenant_id=tenant, service_account_id=account)
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    builder = (x509.CertificateBuilder().subject_name(csr.subject).issuer_name(root.subject).public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + timedelta(days=1) if future else now - timedelta(days=1) if expired else now - timedelta(minutes=1))
        .not_valid_after(now - timedelta(hours=1) if expired else now + timedelta(days=days)))
    for extension in csr.extensions:
        value = extension.value
        if server_auth and isinstance(value, x509.ExtendedKeyUsage):
            value = x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH])
        if ca_leaf and isinstance(value, x509.BasicConstraints):
            value = x509.BasicConstraints(ca=True, path_length=None)
        if wrong_dns and isinstance(value, x509.SubjectAlternativeName):
            value = x509.SubjectAlternativeName([x509.DNSName("device.foreign.identity.invalid")])
        builder = builder.add_extension(value, extension.critical)
    leaf = (builder.add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), False)
        .sign(root_key, hashes.SHA256()))
    pem = root.public_bytes(serialization.Encoding.PEM).decode()
    trust = ServiceAccountTrustBundle("https://platform.example", "https://mtls.platform.example:8443",
                                      pem, sha256(pem.encode()).hexdigest())
    payload = {"operation_id": str(uuid4()), "status": "awaiting_activation", "service_account_id": str(account),
        "integration_id": str(integration), "certificate_pem": leaf.public_bytes(serialization.Encoding.PEM).decode(),
        "certificate_chain_pem": pem, "certificate_sha256": sha256(leaf.public_bytes(serialization.Encoding.DER)).hexdigest(),
        "expires_at": leaf.not_valid_after_utc.isoformat()}
    args = dict(platform_origin=trust.platform_origin, tenant_id=tenant, service_account_id=account,
        integration_id=integration, csr_pem=csr_pem, trust=trust, payload=payload,
        approved_public_key_der=key.public_key().public_bytes(serialization.Encoding.DER,
                                                            serialization.PublicFormat.SubjectPublicKeyInfo))
    return SimpleNamespace(args=args, key=key, leaf=leaf, root=root, root_key=root_key)
