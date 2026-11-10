# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Locally owned, separate setup proof and proposed TLS key material."""

import base64
from dataclasses import dataclass, field
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa


def _plain_text(value: str, limit: int) -> None:
    if (not isinstance(value, str) or not value or value != value.strip()
        or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("bounded plain display text required")


@dataclass(frozen=True)
class SetupKeys:
    proof_key: ed25519.Ed25519PrivateKey = field(repr=False)
    tls_key: rsa.RSAPrivateKey = field(repr=False)

    def __post_init__(self):
        if (not isinstance(self.proof_key, ed25519.Ed25519PrivateKey)
            or not isinstance(self.tls_key, rsa.RSAPrivateKey) or self.tls_key.key_size != 2048):
            raise ValueError("Ed25519 proof and RSA-2048 TLS keys required")

    @classmethod
    def generate(cls) -> "SetupKeys":
        return cls(ed25519.Ed25519PrivateKey.generate(),
            rsa.generate_private_key(public_exponent=65537, key_size=2048))

    def proposal(self, *, proposal_id: UUID, kind: str, display_name: str,
        client_version: str, platform: str | None = None) -> dict:
        if (not isinstance(proposal_id, UUID) or kind not in {"device", "workload"}
            or (kind == "device" and platform not in {"macos", "windows", "linux"})
            or (kind == "workload" and platform is not None)):
            raise ValueError("exact setup kind and platform required")
        _plain_text(display_name, 128)
        _plain_text(client_version, 64)
        proof = self.proof_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        tls = self.tls_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        return {"id": str(proposal_id), "kind": kind, "display_name": display_name,
            "platform": platform, "client_version": client_version,
            "proof_public_key_raw": base64.urlsafe_b64encode(proof).rstrip(b"=").decode("ascii"),
            "tls_public_key_der_base64": base64.b64encode(tls).decode("ascii")}
