# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Closed native proposal facts, never discovered arbitrary runtime commands."""

import base64
from dataclasses import dataclass
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .contract_values import canonical_id
from .native_settings import NativeSettings


@dataclass(frozen=True)
class NativeProposal:
    id: UUID
    display_name: str
    packaging: str
    native_host: str
    enforcement_mode: str
    coverage_posture: str
    tls_public_key_der_base64: str

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict) or set(payload) != {"id", "display_name", "profile",
                "enforcement_mode", "coverage_posture", "tls_public_key_der_base64"}:
            raise ValueError("closed native proposal required")
        settings = NativeSettings.from_payload({name: payload[name] for name in (
            "display_name", "profile", "enforcement_mode", "coverage_posture")})
        encoded = payload["tls_public_key_der_base64"]
        if not isinstance(encoded, str) or not 1 <= len(encoded) <= 4096:
            raise ValueError("bounded retained public key required")
        der = base64.b64decode(encoded, validate=True)
        key = serialization.load_der_public_key(der)
        if (not isinstance(key, rsa.RSAPublicKey) or key.key_size != 2048
                or base64.b64encode(key.public_bytes(serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo)).decode() != encoded):
            raise ValueError("canonical RSA integration public key required")
        return cls(canonical_id(payload["id"]), settings.display_name, settings.packaging, settings.native_host,
                   settings.enforcement_mode, settings.coverage_posture, encoded)

    def payload(self):
        return {"id": str(self.id), "display_name": self.display_name,
            "profile": {"account_type": "agent", "packaging": self.packaging, "native_host": self.native_host},
            "enforcement_mode": self.enforcement_mode, "coverage_posture": self.coverage_posture,
            "tls_public_key_der_base64": self.tls_public_key_der_base64}
