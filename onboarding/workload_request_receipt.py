# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Closed workload acknowledgement; submission never means approval/readiness."""

from dataclasses import dataclass
import base64
from datetime import datetime
from hashlib import sha256
import re
from uuid import UUID

from .contract_values import aware_time, canonical_id
from .errors import OnboardingClientError
from .proofs import canonical_json


@dataclass(frozen=True)
class WorkloadRequestReceipt:
    id: UUID
    kind: str
    status: str
    device_id: UUID | None
    revision: int
    intent_sha256: str
    service_account_id: UUID
    integration_id: UUID
    expires_at: datetime
    claim_expires_at: datetime | None

    def payload(self):
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        for name in ("id", "service_account_id", "integration_id"):
            result[name] = str(result[name])
        result["device_id"] = None if self.device_id is None else str(self.device_id)
        result["expires_at"] = self.expires_at.isoformat()
        result["claim_expires_at"] = None if self.claim_expires_at is None else self.claim_expires_at.isoformat()
        return result

    @classmethod
    def from_payload(cls, payload, *, draft):
        try:
            if not isinstance(payload, dict) or set(payload) != set(cls.__dataclass_fields__):
                raise ValueError("closed workload acknowledgement required")
            values = dict(payload)
            for name in ("id", "service_account_id", "integration_id"):
                values[name] = canonical_id(payload[name])
            values["device_id"] = None if payload["device_id"] is None else canonical_id(payload["device_id"])
            if (values["id"] != draft.proposal.id or values["device_id"] != draft.proposal.device_id
                    or payload["kind"] != "workload" or type(payload["revision"]) is not int
                    or payload["revision"] != 1 or payload["status"] not in ("pending", "approved", "issuing",
                        "issued", "installed", "connected", "denied", "cancelled", "expired", "failed")
                    or not isinstance(payload["intent_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", payload["intent_sha256"])):
                raise ValueError("exact workload acknowledgement required")
            setup = draft.setup
            intent = {"request_id": str(draft.proposal.id), "revision": 1, "kind": "workload",
                "tenant_id": str(setup.tenant_id), "setup_operator_id": str(setup.owner_user_id),
                "onboarding_session_id": str(setup.operation_id),
                "service_account_id": str(values["service_account_id"]),
                "integration_id": str(values["integration_id"]), "ongoing_authority": "machine",
                "proposal": draft.proposal.payload(), "tls_public_key_sha256": sha256(base64.b64decode(
                    setup.proposal()["tls_public_key_der_base64"], validate=True)).hexdigest()}
            if sha256(canonical_json(intent)).hexdigest() != payload["intent_sha256"]:
                raise ValueError("workload acknowledgement changed frozen intent")
            values["expires_at"] = aware_time(payload["expires_at"])
            values["claim_expires_at"] = None if payload["claim_expires_at"] is None else aware_time(payload["claim_expires_at"])
            return cls(**values)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise OnboardingClientError("onboarding_workload_receipt_invalid") from exc
