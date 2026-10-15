"""Closed key-bound preparation pages reject cross-bootstrap delivery."""

import base64
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from .contract_values import canonical_id
from .errors import OnboardingClientError
from .workload_preparation import WorkloadPreparation


@dataclass(frozen=True)
class WorkloadPreparationPage:
    preparations: tuple[WorkloadPreparation, ...]
    next_after: UUID | None

    @classmethod
    def from_payload(cls, payload, *, setup, after=None):
        try:
            if (not isinstance(payload, dict) or set(payload) != {"session_id", "tenant_id", "operator_user_id",
                    "proof_public_key_sha256", "tls_public_key_sha256", "preparations", "next_after"}
                    or not isinstance(payload["preparations"], list) or len(payload["preparations"]) > 25):
                raise ValueError("Closed bounded workload preparation page required")
            keys = setup.proposal()
            proof_digest = sha256(base64.urlsafe_b64decode(keys["proof_public_key_raw"] + "=")).hexdigest()
            tls_digest = sha256(base64.b64decode(keys["tls_public_key_der_base64"], validate=True)).hexdigest()
            if ((canonical_id(payload["session_id"]), canonical_id(payload["tenant_id"]), canonical_id(payload["operator_user_id"]))
                    != (setup.operation_id, setup.tenant_id, setup.owner_user_id)
                    or payload["proof_public_key_sha256"] != proof_digest or payload["tls_public_key_sha256"] != tls_digest):
                raise ValueError("Exact retained workload bootstrap and keys required")
            items = tuple(WorkloadPreparation.from_payload(item) for item in payload["preparations"])
            ids = [item.id for item in items]
            cursor = None if payload["next_after"] is None else canonical_id(payload["next_after"])
            if (ids != sorted(set(ids)) or after is not None and any(identifier <= after for identifier in ids)
                    or cursor is not None and (len(items) != 25 or cursor != ids[-1])):
                raise ValueError("Exact advancing workload preparation page required")
            return cls(items, cursor)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise OnboardingClientError("onboarding_workload_preparation_response_invalid") from exc
