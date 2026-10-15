# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Activation history is not a current execution grant or host readiness."""

from dataclasses import dataclass
import json
from uuid import UUID

from .atomic_private_file import create_private_file, read_private_file
from .contract_values import canonical_id
from .errors import OnboardingClientError
from .proofs import canonical_json


@dataclass(frozen=True)
class NativeActivationResult:
    authorization_id: UUID
    authorization_epoch: int
    runtime_status: str

    @classmethod
    def from_payload(cls, payload, *, stored):
        try:
            credential = stored.credential
            if (not isinstance(payload, dict) or set(payload) != {"operation_id", "service_account_id", "integration_id",
                    "authorization_id", "authorization_epoch", "status", "runtime_status"}
                    or payload["operation_id"] != str(credential.operation_id)
                    or payload["service_account_id"] != str(credential.service_account_id)
                    or payload["integration_id"] != str(credential.integration_id)
                    or payload["status"] != "credential_active"
                    or payload["runtime_status"] not in ("awaiting_registration", "registered")
                    or type(payload["authorization_epoch"]) is not int
                    or not 1 <= payload["authorization_epoch"] <= 2**31 - 1):
                raise ValueError("exact native activation result required")
            return cls(canonical_id(payload["authorization_id"]), payload["authorization_epoch"], payload["runtime_status"])
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise OnboardingClientError("onboarding_native_activation_response_invalid") from exc


def _activation_record(*, draft, assignment, stored, result):
    credential = stored.credential
    return {"revision": 1, "request_id": str(draft.proposal.id), "setup_id": str(draft.setup_id),
        "tenant_id": str(draft.tenant_id), "device_id": str(draft.device_id), "owner_user_id": str(draft.owner_user_id),
        "intent_sha256": assignment.intent_sha256, "operation_id": str(credential.operation_id),
        "service_account_id": str(credential.service_account_id), "integration_id": str(credential.integration_id),
        "certificate_sha256": credential.certificate_sha256,
        "authorization_id": str(result.authorization_id), "authorization_epoch": result.authorization_epoch}


def load_native_activation_receipt(*, draft, assignment, stored):
    """Recover history without reactivating an obsolete initial certificate."""
    try:
        record = json.loads(read_private_file(stored.certificate_path.parent / "activation.json", limit=4096))
        epoch = record["authorization_epoch"]
        if type(epoch) is not int or not 1 <= epoch <= 2**31 - 1:
            raise ValueError("exact retained authorization generation required")
        result = NativeActivationResult(canonical_id(record["authorization_id"]), epoch,
            "registered" if assignment.status == "connected" else "awaiting_registration")
        if assignment.status not in {"installed", "connected"} or record != _activation_record(
                draft=draft, assignment=assignment, stored=stored, result=result):
            raise ValueError("exact retained activation required")
        return result
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise OnboardingClientError("onboarding_native_activation_receipt_unavailable") from exc


def save_native_activation_receipt(*, draft, assignment, stored, result):
    record = _activation_record(draft=draft, assignment=assignment, stored=stored, result=result)
    path = stored.certificate_path.parent / "activation.json"
    try:
        try:
            create_private_file(path, canonical_json(record))
        except FileExistsError:
            pass  # Recovery must match the same activation, never overwrite another receipt.
        current = json.loads(read_private_file(path, limit=4096))
        if (not isinstance(current, dict) or type(current.get("revision")) is not int
                or type(current.get("authorization_epoch")) is not int or current != record):
            raise ValueError("committed native activation changed")
    except (OSError, ValueError, TypeError) as exc:
        raise OnboardingClientError("onboarding_native_activation_receipt_unavailable") from exc
