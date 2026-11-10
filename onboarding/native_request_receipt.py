# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Exact submission acknowledgement, not approval or execution authority."""

from dataclasses import dataclass
from datetime import datetime
import re
from uuid import UUID

from .contract_values import aware_time, canonical_id
from .errors import OnboardingClientError


@dataclass(frozen=True)
class NativeRequestReceipt:
    id: UUID
    kind: str
    status: str
    device_id: UUID
    revision: int
    intent_sha256: str
    service_account_id: UUID
    integration_id: UUID
    expires_at: datetime
    claim_expires_at: datetime | None

    @classmethod
    def from_payload(cls, payload, *, draft):
        try:
            if not isinstance(payload, dict) or set(payload) != set(cls.__dataclass_fields__):
                raise ValueError("closed request receipt required")
            values = dict(payload)
            for field in ("id", "device_id", "service_account_id", "integration_id"):
                values[field] = canonical_id(payload[field])
            if (values["id"] != draft.proposal.id or values["device_id"] != draft.device_id
                    or payload["kind"] != "native_connection"
                    or type(payload["revision"]) is not int or payload["revision"] != 1
                    or payload["status"] not in ("pending", "approved", "issuing", "issued", "installed",
                        "connected", "denied", "cancelled", "expired", "failed")
                    or not isinstance(payload["intent_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", payload["intent_sha256"])):
                raise ValueError("exact native request acknowledgement required")
            values["expires_at"] = aware_time(payload["expires_at"])
            values["claim_expires_at"] = None if payload["claim_expires_at"] is None else aware_time(payload["claim_expires_at"])
            return cls(**values)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise OnboardingClientError("onboarding_native_receipt_invalid") from exc
