# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Device leaf identity/purpose is exact, not inferred from arbitrary SAN content."""

from datetime import timedelta
from hashlib import sha256
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def validate_device_leaf_identity(leaf, *, tenant_id: UUID, device_id: UUID,
                                  public_key_der: bytes) -> None:
    """Validate the same closed leaf profile independently of setup history."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    if not isinstance(tenant_id, UUID) or not isinstance(device_id, UUID):
        raise ValueError("exact assigned device IDs required")
    key = public_key_der
    public = serialization.load_der_public_key(key)
    if (not isinstance(public, rsa.RSAPublicKey) or public.key_size != 2048
            or public.public_bytes(serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo) != key):
        raise ValueError("canonical device RSA-2048 key required")
    if leaf.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != key:
        raise ValueError("issued key differs from proposed local TLS key")
    digest = sha256(key).hexdigest()
    expected = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"device:{device_id}:{digest[:16]}")])
    if leaf.subject != expected:
        raise ValueError("exact device subject required")
    dns = f"device.{device_id.hex}.{tenant_id.hex}.identity.invalid"
    if list(leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value) != [x509.DNSName(dns)]:
        raise ValueError("exact device DNS identity required")
    usages = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    constraints = leaf.extensions.get_extension_for_class(x509.BasicConstraints).value
    if list(usages) != [ExtendedKeyUsageOID.CLIENT_AUTH] or constraints.ca or constraints.path_length is not None:
        raise ValueError("device client-only leaf authority required")
    if leaf.not_valid_after_utc - leaf.not_valid_before_utc > timedelta(days=90, minutes=20):
        raise ValueError("device certificate lifetime exceeds profile")
