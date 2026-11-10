# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Service-account CSR from an already persisted, separate integration TLS key."""

from dataclasses import dataclass
from hashlib import sha256
import re
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


@dataclass(frozen=True)
class ServiceAccountCsr:
    pem: str
    csr_sha256: str
    public_key_sha256: str
    tenant_id: UUID
    service_account_id: UUID


def service_account_csr(private_key, *, tenant_id: UUID, service_account_id: UUID) -> str:
    if (not isinstance(private_key, rsa.RSAPrivateKey) or private_key.key_size != 2048
        or not isinstance(tenant_id, UUID) or not isinstance(service_account_id, UUID)):
        raise ValueError("persisted RSA-2048 key and exact account identities required")
    digest = sha256(private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
    csr = (x509.CertificateSigningRequestBuilder().subject_name(x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"sa:{service_account_id}:{digest[:16]}")]))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(
            f"sa.{service_account_id.hex}.{tenant_id.hex}.identity.invalid")]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256()))
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


def service_account_csr_facts(pem: str) -> ServiceAccountCsr:
    try:
        return _service_account_csr_facts(pem)
    except (x509.ExtensionNotFound, x509.DuplicateExtension) as exc:
        raise ValueError("complete unambiguous service-account profile required") from exc


def _service_account_csr_facts(pem: str) -> ServiceAccountCsr:
    if not isinstance(pem, str) or not 1 <= len(pem.encode("ascii")) <= 32768:
        raise ValueError("bounded canonical CSR required")
    csr = x509.load_pem_x509_csr(pem.encode("ascii"))
    if not csr.is_signature_valid or csr.public_bytes(serialization.Encoding.PEM).decode().strip() != pem.strip():
        raise ValueError("single signed canonical CSR required")
    key = csr.public_key()
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size != 2048:
        raise ValueError("RSA-2048 profile required")
    digest = sha256(key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
    names = list(csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value)
    match = re.fullmatch(r"sa\.([0-9a-f]{32})\.([0-9a-f]{32})\.identity\.invalid", names[0].value) if (
        len(names) == 1 and isinstance(names[0], x509.DNSName)) else None
    if match is None:
        raise ValueError("exact service-account DNS identity required")
    account, tenant = (UUID(hex=item) for item in match.groups())
    constraints = csr.extensions.get_extension_for_class(x509.BasicConstraints).value
    if (csr.subject != x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"sa:{account}:{digest[:16]}")])
        or list(csr.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) != [ExtendedKeyUsageOID.CLIENT_AUTH]
        or constraints.ca or constraints.path_length is not None or len(csr.extensions) != 3):
        raise ValueError("exact client-only service-account profile required")
    return ServiceAccountCsr(csr.public_bytes(serialization.Encoding.PEM).decode(),
        sha256(csr.public_bytes(serialization.Encoding.DER)).hexdigest(), digest, tenant, account)
