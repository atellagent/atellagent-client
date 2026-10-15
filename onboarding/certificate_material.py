# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Certificate-only bounded canonical PEM parsing, never private key adoption."""

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.x509.verification import PolicyBuilder, Store
from datetime import datetime, timezone


def parse_certificates(pem: str, *, maximum: int) -> list[x509.Certificate]:
    if not isinstance(pem, str) or not 1 <= len(pem.encode("ascii")) <= 32768:
        raise ValueError("bounded certificate material required")
    certificates = x509.load_pem_x509_certificates(pem.encode("ascii"))
    canonical = b"".join(cert.public_bytes(serialization.Encoding.PEM) for cert in certificates)
    if not 1 <= len(certificates) <= maximum or canonical.strip() != pem.encode("ascii").strip():
        raise ValueError("canonical certificate-only material required")
    if len({cert.public_bytes(serialization.Encoding.DER) for cert in certificates}) != len(certificates):
        raise ValueError("duplicate certificate material")
    return certificates


def verify_client_chain(leaf, chain_pem: str, trusted_ca_pem: str, *, verification_time=None):
    """Validate against independent trust, never a response-supplied root."""
    chain = parse_certificates(chain_pem, maximum=8)
    anchors = parse_certificates(trusted_ca_pem, maximum=8)
    verified = (PolicyBuilder().store(Store(anchors)).time(verification_time or datetime.now(timezone.utc))
        .max_chain_depth(3).build_client_verifier().verify(leaf, chain))
    verified_der = {cert.public_bytes(serialization.Encoding.DER) for cert in verified.chain[1:]}
    remaining = [cert for cert in chain if cert.public_bytes(serialization.Encoding.DER) not in verified_der]
    anchor_der = {cert.public_bytes(serialization.Encoding.DER) for cert in anchors}
    tail = verified.chain[-1]
    # Verification may stop at a trusted intermediate. A returned parent root
    # must itself be independently trusted AND directly sign that intermediate.
    while remaining:
        parent = next((cert for cert in remaining if cert.subject == tail.issuer
            and cert.public_bytes(serialization.Encoding.DER) in anchor_der), None)
        if parent is None:
            raise ValueError("unrelated returned chain material")
        try:
            tail.verify_directly_issued_by(parent)
        except (InvalidSignature, ValueError) as exc:
            raise ValueError("unrelated returned chain material") from exc
        remaining.remove(parent)
        tail = parent
    return verified
