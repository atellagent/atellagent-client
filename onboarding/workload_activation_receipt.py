# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Durable activation history is not a current runtime-health or policy grant."""

from dataclasses import dataclass
import json

from .atomic_private_file import create_private_file, read_private_file
from .errors import OnboardingClientError
from .proofs import canonical_json
from .workload_credential_record import workload_credential_metadata


@dataclass(frozen=True)
class WorkloadActivationResult:
    runtime_status: str

    @classmethod
    def from_payload(cls, payload, *, stored):
        credential = stored.credential
        if (not isinstance(payload, dict) or set(payload) != {"operation_id", "service_account_id", "integration_id",
                "status", "runtime_status"} or payload["operation_id"] != str(credential.operation_id)
                or payload["service_account_id"] != str(credential.service_account_id)
                or payload["integration_id"] != str(credential.integration_id)
                or payload["status"] != "credential_active"
                or payload["runtime_status"] not in ("awaiting_registration", "registered")):
            raise OnboardingClientError("onboarding_workload_activation_response_invalid")
        return cls(payload["runtime_status"])


def require_workload_activation_receipt(*, draft, receipt, stored):
    expected = workload_credential_metadata(draft, receipt, stored.credential)
    try:
        record = json.loads(read_private_file(stored.certificate_path.parent / "activation.json", limit=4096))
        if not isinstance(record, dict) or type(record.get("revision")) is not int or record != expected:
            raise ValueError("exact committed workload activation required")
    except (OSError, ValueError, TypeError) as exc:
        raise OnboardingClientError("onboarding_workload_activation_receipt_unavailable") from exc


def save_workload_activation_receipt(*, draft, receipt, stored):
    record = workload_credential_metadata(draft, receipt, stored.credential)
    try:
        create_private_file(stored.certificate_path.parent / "activation.json", canonical_json(record))
    except FileExistsError:
        pass  # Retry must match exactly; never overwrite an activation history.
    except (OSError, ValueError) as exc:
        raise OnboardingClientError("onboarding_workload_activation_receipt_unavailable") from exc
    require_workload_activation_receipt(draft=draft, receipt=receipt, stored=stored)
