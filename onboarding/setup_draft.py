# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Immutable pre-submission intent and keys, sufficient to resume the same setup."""

from dataclasses import dataclass, field
import json
from uuid import UUID

from .bootstrap_origin import bootstrap_origin
from .contract_values import canonical_id
from .proofs import canonical_json
from .setup_key_encoding import decode_keys, encode_keys
from .setup_keys import SetupKeys


@dataclass(frozen=True)
class SetupDraft:
    operation_id: UUID
    tenant_id: UUID
    owner_user_id: UUID
    platform_origin: str
    kind: str
    display_name: str
    client_version: str
    platform: str | None
    keys: SetupKeys = field(repr=False)

    def __post_init__(self):
        if not all(isinstance(value, UUID) for value in (self.operation_id, self.tenant_id, self.owner_user_id)):
            raise ValueError("exact setup identities required")
        bootstrap_origin(self.platform_origin)
        self.proposal()

    def proposal(self) -> dict:
        return self.keys.proposal(proposal_id=self.operation_id, kind=self.kind,
            display_name=self.display_name, client_version=self.client_version, platform=self.platform)

    def encode(self) -> bytes:
        metadata = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "keys"}
        for name in ("operation_id", "tenant_id", "owner_user_id"):
            metadata[name] = str(metadata[name])
        return canonical_json({"revision": 1, **metadata, **encode_keys(self.keys)})

    @classmethod
    def decode(cls, raw: bytes) -> "SetupDraft":
        if not 1 <= len(raw) <= 32768:
            raise ValueError("bounded setup draft required")
        payload = json.loads(raw)
        expected = (set(cls.__dataclass_fields__) - {"keys"}) | {"revision", "proof_key_pem", "tls_key_pem"}
        if (not isinstance(payload, dict) or set(payload) != expected
            or type(payload["revision"]) is not int or payload["revision"] != 1):
            raise ValueError("closed current setup draft required")
        keys = decode_keys(payload)
        values = {name: payload[name] for name in cls.__dataclass_fields__ if name != "keys"}
        for name in ("operation_id", "tenant_id", "owner_user_id"):
            values[name] = canonical_id(values[name])
        return cls(**values, keys=keys)
