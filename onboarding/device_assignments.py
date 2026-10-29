# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Exact own-device inventory; a listed request is not execution permission."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import re
from uuid import UUID

from .contract_values import canonical_id, aware_time
from .errors import OnboardingClientError
from .native_proposal import NativeProposal
from .proofs import canonical_json

IDENTITIES = ("request_id", "onboarding_session_id", "service_account_id", "integration_id",
              "agent_principal_id", "binding_id", "controller_profile_id")


@dataclass(frozen=True)
class DeviceAssignment:
    request_id: UUID
    onboarding_session_id: UUID
    service_account_id: UUID
    integration_id: UUID
    agent_principal_id: UUID
    binding_id: UUID
    controller_profile_id: UUID
    revision: int
    status: str
    intent_sha256: str
    proposal: NativeProposal
    host_adapter_version: str
    delegation_lifetime: str
    capabilities: tuple[str, ...]
    expires_at: datetime
    claim_expires_at: datetime | None
    owner_consented_at: datetime | None

    def payload(self):
        result = {field: getattr(self, field) for field in self.__dataclass_fields__}
        result.update({field: str(getattr(self, field)) for field in IDENTITIES})
        result.update(proposal=self.proposal.payload(), capabilities=list(self.capabilities),
            expires_at=self.expires_at.isoformat(),
            claim_expires_at=None if self.claim_expires_at is None else self.claim_expires_at.isoformat(),
            owner_consented_at=None if self.owner_consented_at is None else self.owner_consented_at.isoformat())
        return result

    @classmethod
    def from_payload(cls, payload, *, device):
        if not isinstance(payload, dict) or set(payload) != set(cls.__dataclass_fields__):
            raise ValueError("closed assignment required")
        values = dict(payload)
        for field in IDENTITIES:
            values[field] = canonical_id(payload[field])
        if type(payload["revision"]) is not int or payload["revision"] != 1:
            raise ValueError("current exact revision required")
        if payload["status"] not in ("pending", "approved", "issuing", "issued", "installed",
                                     "connected", "denied", "cancelled", "expired", "failed"):
            raise ValueError("known assignment stage required")
        digest = payload["intent_sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("reviewed intent digest required")
        if (payload["host_adapter_version"] != "atellagent.host-hooks.v1"
                or payload["delegation_lifetime"] != "until_revoked"
                or payload["capabilities"] != ["agent.control"]):
            raise ValueError("reviewed native authority boundary required")
        proposal = NativeProposal.from_payload(payload["proposal"])
        if proposal.id != values["request_id"]:
            raise ValueError("exact request proposal required")
        intent = {field: str(values[field]) for field in IDENTITIES if field != "onboarding_session_id"}
        intent.update(revision=1, tenant_id=str(device.tenant_id), owner_user_id=str(device.owner_user_id),
            device_id=str(device.device_id), proposal=proposal.payload(), capabilities=["agent.control"],
            host_adapter_version=payload["host_adapter_version"], delegation_lifetime="until_revoked")
        if sha256(canonical_json(intent)).hexdigest() != digest:
            raise ValueError("intent does not belong to exact device owner")
        values.update(proposal=proposal, capabilities=("agent.control",),
            expires_at=aware_time(payload["expires_at"]),
            claim_expires_at=None if payload["claim_expires_at"] is None else aware_time(payload["claim_expires_at"]),
            owner_consented_at=None if payload["owner_consented_at"] is None else aware_time(payload["owner_consented_at"]))
        return cls(**values)


@dataclass(frozen=True)
class DeviceAssignmentPage:
    assignments: tuple[DeviceAssignment, ...]
    next_after: UUID | None

    @classmethod
    def from_payload(cls, payload, *, device, after=None):
        try:
            if (not isinstance(payload, dict) or set(payload) != {"assignments", "next_after"}
                    or not isinstance(payload["assignments"], list) or len(payload["assignments"]) > 8):
                raise ValueError("bounded assignment page required")
            items = tuple(DeviceAssignment.from_payload(item, device=device) for item in payload["assignments"])
            ids = [item.request_id for item in items]
            if ids != sorted(set(ids)) or (after is not None and any(item <= after for item in ids)):
                raise ValueError("strict advancing assignment page required")
            cursor = None if payload["next_after"] is None else canonical_id(payload["next_after"])
            if cursor is not None and (len(items) != 8 or cursor != ids[-1]):
                raise ValueError("exact continuation required")
            return cls(items, cursor)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise OnboardingClientError("device_assignment_response_invalid") from exc
