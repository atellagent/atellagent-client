"""Closed pickup progress acknowledgement, not approved execution authority."""

import re

from .contract_values import aware_time, canonical_id
from .errors import OnboardingClientError


def pickup_intent_digest(payload, *, request_id):
    try:
        if (not isinstance(payload, dict) or set(payload) != {"id", "status", "revision", "intent_sha256", "expires_at"}
                or canonical_id(payload["id"]) != request_id
                or type(payload["revision"]) is not int or payload["revision"] != 1
                or payload["status"] not in ("pending", "approved", "issuing", "issued", "installed",
                    "connected", "denied", "cancelled", "expired", "failed")
                or not isinstance(payload["intent_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", payload["intent_sha256"])):
            raise ValueError("Exact pickup acknowledgement required")
        aware_time(payload["expires_at"])
        return payload["intent_sha256"]
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise OnboardingClientError("onboarding_native_receipt_invalid") from exc
