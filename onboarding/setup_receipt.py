# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Creation receipt is pending setup, never proof of installed/connected authority."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .contract_values import aware_time, canonical_id
from .errors import OnboardingClientError


@dataclass(frozen=True)
class SetupReceipt:
    id: UUID
    kind: str
    status: str
    device_id: UUID | None
    revision: int
    expires_at: datetime
    claim_expires_at: datetime | None

    @classmethod
    def from_payload(cls, payload: dict, *, operation_id: UUID, kind: str) -> "SetupReceipt":
        try:
            if (set(payload) != set(cls.__dataclass_fields__) or payload["id"] != str(operation_id)
                or payload["kind"] != kind or kind not in {"device", "workload"}
                or payload["status"] not in {"pending", "approved", "denied", "cancelled", "expired", "consumed", "failed"}
                or type(payload["revision"]) is not int or payload["revision"] != 1):
                raise ValueError("closed exact setup receipt required")
            device = None if payload["device_id"] is None else canonical_id(payload["device_id"])
            if (kind == "device") != (device is not None):
                raise ValueError("exact device/workload identity required")
            claim = None if payload["claim_expires_at"] is None else aware_time(payload["claim_expires_at"])
            if payload["status"] in {"approved", "consumed"} and claim is None:
                raise ValueError("approved claim deadline required")
            return cls(canonical_id(payload["id"]), kind, payload["status"], device, 1,
                aware_time(payload["expires_at"]), claim)
        except (ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_response_invalid") from exc
