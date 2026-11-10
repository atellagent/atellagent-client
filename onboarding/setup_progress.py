# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Closed proof-bound progress; never interpreted as machine admission."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .contract_values import canonical_id, aware_time
from .device_csr import device_certificate_operation_id
from .errors import OnboardingClientError


@dataclass(frozen=True)
class SetupProgress:
    id: UUID
    kind: str
    status: str
    device_id: UUID | None
    expires_at: datetime
    claim_expires_at: datetime | None
    device_issuance: dict | None

    @classmethod
    def from_payload(cls, payload, *, operation_id):
        try:
            if (not isinstance(payload, dict) or set(payload) != set(cls.__dataclass_fields__)
                or payload["id"] != str(operation_id) or payload["kind"] not in {"device", "workload"}
                or payload["status"] not in {"pending", "approved", "denied", "cancelled", "expired", "consumed", "failed"}):
                raise ValueError("closed exact progress required")
            device = None if payload["device_id"] is None else canonical_id(payload["device_id"])
            if (payload["kind"] == "device") != (device is not None):
                raise ValueError("exact setup kind required")
            issuance = payload["device_issuance"]
            if issuance is not None:
                cls._require_issuance(issuance, operation_id)
                if device is None:
                    raise ValueError("device progress requires device")
            claim = None if payload["claim_expires_at"] is None else aware_time(payload["claim_expires_at"])
            if payload["status"] in {"approved", "consumed"} and claim is None:
                raise ValueError("claim deadline required")
            return cls(canonical_id(payload["id"]), payload["kind"], payload["status"], device,
                aware_time(payload["expires_at"]), claim, issuance)
        except (ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_response_invalid") from exc

    @staticmethod
    def _require_issuance(value, operation_id):
        stages = {"pending": "queued", "in_progress": "issuing", "issued": "awaiting_activation",
            "succeeded": "device_ready", "failed": "failed", "cancelled": "cancelled"}
        reasons = {None, "device_issuance_budget_exhausted", "device_issuance_rejected",
            "device_issuer_unavailable", "device_admission_unavailable"}
        if (not isinstance(value, dict) or set(value) != {"operation_id", "status", "stage", "reason_code",
            "attempt_count", "next_attempt_at"}
            or value["operation_id"] != str(device_certificate_operation_id(operation_id))
            or not isinstance(value["status"], str) or value["status"] not in stages
            or value["stage"] != stages[value["status"]]
            or not isinstance(value["reason_code"], (str, type(None))) or value["reason_code"] not in reasons
            or type(value["attempt_count"]) is not int or not 0 <= value["attempt_count"] <= 10000):
            raise ValueError("closed exact device issuance required")
        if value["status"] == "pending":
            aware_time(value["next_attempt_at"])
        elif value["next_attempt_at"] is not None:
            raise ValueError("unexpected issuance retry timestamp")
