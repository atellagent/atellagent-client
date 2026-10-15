# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Membership-backed explicit selection, not a tenant directory or approval grant."""

from dataclasses import dataclass
from uuid import UUID

from .contract_values import canonical_id
from .errors import OnboardingClientError


@dataclass(frozen=True)
class SetupMembership:
    tenant_id: UUID
    tenant_name: str
    can_approve: bool


@dataclass(frozen=True)
class SetupIdentity:
    user_id: UUID
    memberships: tuple[SetupMembership, ...]

    @classmethod
    def from_payload(cls, payload: dict) -> "SetupIdentity":
        try:
            if set(payload) != {"user_id", "memberships", "selection_required"} or payload["selection_required"] is not True:
                raise ValueError("explicit membership selection required")
            rows = payload["memberships"]
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError("bounded memberships required")
            memberships = []
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"tenant_id", "tenant_name", "can_approve"}:
                    raise ValueError("closed membership required")
                name = row["tenant_name"]
                if (not isinstance(name, str) or not 1 <= len(name) <= 256
                    or any(ord(c) < 32 or ord(c) == 127 for c in name) or type(row["can_approve"]) is not bool):
                    raise ValueError("bounded membership details required")
                memberships.append(SetupMembership(canonical_id(row["tenant_id"]), name, row["can_approve"]))
            if len({item.tenant_id for item in memberships}) != len(memberships):
                raise ValueError("duplicate membership")
            return cls(canonical_id(payload["user_id"]), tuple(memberships))
        except (ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_identity_invalid") from exc

    def require_selection(self, *, expected_user_id: UUID, tenant_id: UUID) -> None:
        if self.user_id != expected_user_id:
            raise OnboardingClientError("onboarding_account_changed")
        if not isinstance(tenant_id, UUID) or not any(item.tenant_id == tenant_id for item in self.memberships):
            raise OnboardingClientError("onboarding_membership_required")
