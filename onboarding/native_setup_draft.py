# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Frozen native proposal and retained integration key, saved before submission."""

import base64
from dataclasses import dataclass, field
import json
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from atellagent_client.sdk.private_keys import load_private_key

from .bootstrap_origin import bootstrap_origin
from .contract_values import canonical_id
from .native_proposal import NativeProposal
from .proofs import canonical_json


@dataclass(frozen=True)
class NativeSetupDraft:
    setup_id: UUID
    tenant_id: UUID
    device_id: UUID
    owner_user_id: UUID
    platform_origin: str
    proposal: NativeProposal
    tls_key: rsa.RSAPrivateKey = field(repr=False, compare=False)

    def __post_init__(self):
        if any(not isinstance(getattr(self, name), UUID) for name in (
                "setup_id", "tenant_id", "device_id", "owner_user_id")):
            raise ValueError("exact native setup identities required")
        if self.platform_origin != bootstrap_origin(self.platform_origin):
            raise ValueError("trusted canonical platform origin required")
        if not isinstance(self.tls_key, rsa.RSAPrivateKey) or self.tls_key.key_size != 2048:
            raise ValueError("separate retained RSA-2048 integration key required")
        checked = NativeProposal.from_payload(self.proposal.payload())
        if checked != self.proposal or self.proposal.tls_public_key_der_base64 != self.public_key_base64():
            raise ValueError("proposal differs from retained integration key")

    @classmethod
    def create(cls, *, device, setup_id, request_id, platform_origin, display_name, packaging,
               native_host, enforcement_mode, coverage_posture):
        if not isinstance(request_id, UUID):
            raise ValueError("exact native request identity required")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        proposal = NativeProposal.from_payload({"id": str(request_id), "display_name": display_name,
            "profile": {"account_type": "agent", "packaging": packaging, "native_host": native_host},
            "enforcement_mode": enforcement_mode, "coverage_posture": coverage_posture,
            "tls_public_key_der_base64": public})
        return cls(setup_id, device.tenant_id, device.device_id, device.owner_user_id,
                   bootstrap_origin(platform_origin), proposal, key)

    def public_key_base64(self):
        return base64.b64encode(self.tls_key.public_key().public_bytes(serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)).decode()

    def private_key_pem(self):
        return self.tls_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()).decode("ascii")

    def encode(self):
        return canonical_json({"revision": 1, "setup_id": str(self.setup_id), "tenant_id": str(self.tenant_id),
            "device_id": str(self.device_id), "owner_user_id": str(self.owner_user_id),
            "platform_origin": self.platform_origin, "proposal": self.proposal.payload(),
            "tls_key_pem": self.private_key_pem()})

    @classmethod
    def decode(cls, raw):
        payload = json.loads(raw)
        if (not isinstance(payload, dict) or set(payload) != {"revision", "setup_id", "tenant_id", "device_id",
                "owner_user_id", "platform_origin", "proposal", "tls_key_pem"}
                or type(payload["revision"]) is not int or payload["revision"] != 1):
            raise ValueError("closed native setup draft required")
        pem = payload["tls_key_pem"]
        if not isinstance(pem, str) or not 1 <= len(pem) <= 8192:
            raise ValueError("bounded retained integration key required")
        key = load_private_key(pem.encode("ascii"))
        draft = cls(*(canonical_id(payload[name]) for name in (
            "setup_id", "tenant_id", "device_id", "owner_user_id")), payload["platform_origin"],
            NativeProposal.from_payload(payload["proposal"]), key)
        if draft.private_key_pem() != pem:
            raise ValueError("canonical private key required")
        return draft
