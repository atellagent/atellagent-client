# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Explicit owner consent for a reviewed exact assignment, never background approval."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .contract_values import aware_time, canonical_id
from .device_assignments import DeviceAssignment
from .errors import OnboardingClientError
from .http_response import request_json
from .proofs import canonical_json


@dataclass(frozen=True)
class NativeConsentReceipt:
    request_id: UUID
    consented_at: datetime

    @classmethod
    def from_payload(cls, payload, *, assignment):
        try:
            if (not isinstance(payload, dict) or set(payload) != {"id", "status", "revision", "consented_at"}
                    or payload["id"] != str(assignment.request_id) or payload["status"] != "consented"
                    or type(payload["revision"]) is not int or payload["revision"] != assignment.revision):
                raise ValueError("exact owner consent acknowledgement required")
            return cls(canonical_id(payload["id"]), aware_time(payload["consented_at"]))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise OnboardingClientError("onboarding_native_consent_response_invalid") from exc


async def consent_native_assignment(client, *, draft, store, assignment, reviewed_intent_sha256):
    """UI/CLI calls this only after displaying and explicitly confirming the intent.

    Fresh authentication and exact current owner/intent are independently enforced
    by the server. Neither request submission nor admin approval calls this API.
    """
    saved = store.require_saved(draft)
    try:
        checked = DeviceAssignment.from_payload(assignment.payload(), device=saved)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise OnboardingClientError("onboarding_native_setup_intent_changed") from exc
    if (saved.platform_origin != client.origin or checked.proposal != saved.proposal
            or checked.onboarding_session_id != saved.setup_id or checked.status not in ("pending", "approved")
            or not isinstance(reviewed_intent_sha256, str) or reviewed_intent_sha256 != checked.intent_sha256):
        raise OnboardingClientError("onboarding_native_consent_intent_changed")
    identity = await client.identity()
    identity.require_selection(expected_user_id=saved.owner_user_id, tenant_id=saved.tenant_id)
    result = await request_json(client._http, method="POST",
        url=client.origin + f"/api/client-onboarding/owner-consents/{checked.request_id}",
        headers={"Authorization": client._authorization, "Content-Type": "application/json"},
        content=canonical_json({"tenant_id": str(saved.tenant_id), "revision": checked.revision,
                                "intent_sha256": reviewed_intent_sha256}), error_prefix="onboarding")
    return NativeConsentReceipt.from_payload(result, assignment=checked)
