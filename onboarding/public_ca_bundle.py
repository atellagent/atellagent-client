# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Strict public CA material validation; certificate purpose is caller-owned."""

from datetime import datetime, timezone
from hashlib import sha256
from cryptography import x509
from cryptography.hazmat.primitives import serialization


def validate_public_ca_bundle(pem, digest):
    if not isinstance(pem, str) or not 1 <= len(pem.encode("ascii")) <= 32768:
        raise ValueError("bounded public CA bundle required")
    certificates = x509.load_pem_x509_certificates(pem.encode("ascii"))
    canonical = b"".join(cert.public_bytes(serialization.Encoding.PEM) for cert in certificates)
    if not 1 <= len(certificates) <= 8 or canonical.decode("ascii") != pem:
        raise ValueError("canonical public CA bundle required")
    if sha256(canonical).hexdigest() != digest:
        raise ValueError("trust digest mismatch")
    now, fingerprints = datetime.now(timezone.utc), set()
    for cert in certificates:
        if (not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
                or not cert.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign
                or not cert.not_valid_before_utc <= now < cert.not_valid_after_utc):
            raise ValueError("current CA trust required")
        fingerprint = sha256(cert.public_bytes(serialization.Encoding.DER)).digest()
        if fingerprint in fingerprints:
            raise ValueError("duplicate CA trust")
        fingerprints.add(fingerprint)
    return certificates
