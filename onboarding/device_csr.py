# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Exact assigned-device CSR using the persisted proposed TLS key."""

from hashlib import sha256
from uuid import UUID, uuid5

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .setup_draft import SetupDraft
from .setup_receipt import SetupReceipt


def device_certificate_operation_id(setup_id: UUID) -> UUID:
    if not isinstance(setup_id, UUID):
        raise ValueError("exact setup identity required")
    # A predictable operation ID confers no authority; every request proves its key.
    return uuid5(setup_id, "atellagent-device-certificate-v1")


def device_csr(draft: SetupDraft, receipt: SetupReceipt) -> str:
    if (draft.kind != "device" or receipt.kind != "device" or receipt.id != draft.operation_id
        or not isinstance(receipt.device_id, UUID)):
        raise ValueError("exact server-assigned device receipt required")
    return build_device_csr(tenant_id=draft.tenant_id, device_id=receipt.device_id,
        tls_key=draft.keys.tls_key)


def build_device_csr(*, tenant_id: UUID, device_id: UUID, tls_key) -> str:
    """Exact device profile for initial admission or an authorized renewal key."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    if (not isinstance(tenant_id, UUID) or not isinstance(device_id, UUID)
            or not isinstance(tls_key, rsa.RSAPrivateKey) or tls_key.key_size != 2048):
        raise ValueError("exact device IDs and RSA-2048 TLS key required")
    public = tls_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    digest = sha256(public).hexdigest()
    cn = f"device:{device_id}:{digest[:16]}"
    dns = f"device.{device_id.hex}.{tenant_id.hex}.identity.invalid"
    request = (x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(dns)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(tls_key, hashes.SHA256()))
    return request.public_bytes(serialization.Encoding.PEM).decode("ascii")
