# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Safe gateway failure classification for agent control."""

from __future__ import annotations

from contextlib import suppress
from typing import Any
from uuid import UUID


from atellagent_client.sdk.errors import (
    AuthenticationError,
    PolicyTransportError,
    PolicyViolationError,
)
from atellagent_client.sdk.operations_modules.common import extract_policy_detail
from .native_rejection_reasons import NATIVE_REJECTION_MESSAGES, native_rejection_code

class ControlAdmissionError(PolicyViolationError):
    """Admission failed without an authored action-policy decision.

    Remain a refusal for SDK callers and ordinary tools. The hook protocol
    preserves this provenance so exact terminal goal updates can recover.
    """


class NativeConnectionAuthorizationError(ControlAdmissionError):
    """Current device/delegation admission failed before a policy decision.

    Preserve the SDK's refusal contract while keeping this failure distinct
    from an authored policy refusal in the local hook protocol.
    """


_NATIVE_AUTHORITY_CODES = frozenset({
    "local_host_authorization_expired", "local_host_authorization_required",
    "local_host_authorization_revoked", "local_device_user_mismatch",
    "device_binding_invalid", "device_inactive", "device_proof_invalid",
    "device_proof_expired", "device_proof_replayed",
})


# These are intentionally remediation categories rather than server messages.
# Do not promote arbitrary Gateway detail codes into a host-visible error: a
# control response can contain policy or provider diagnostics that are not
# suitable for local hook output.
_SAFE_GATEWAY_REMEDIATION_CODES = frozenset(
    {
        "connection_required",
        "local_host_authorization_expired",
        "local_host_authorization_required",
        "local_host_authorization_revoked",
        "local_device_user_mismatch",
        "device_binding_invalid",
        "device_inactive",
        "device_proof_invalid",
        "device_proof_expired",
        "device_proof_replayed",
        "native_hook_outcome_receipt_expired",
    }
)


# Log-only dependency categories. A response can never acquire policy or
# goal-recovery authority by supplying one of these names.
CONTROL_GATEWAY_FAILURE_CODES = frozenset({
    "service_account_runtime_authority_unavailable",
    "human_runtime_authority_unavailable",
    "local_host_authorization_unavailable",
    "local_host_authorization_storage_unavailable",
    "policy_revision_unavailable",
    "native_policy_roles_unavailable",
    "native_runtime_posture_unavailable",
    "native_preflight_timeout",
    "model_input_capacity_exhausted",
})


def raise_control_response_error(governance: Any, response: Any, payload: Any) -> None:
    """Keep the existing refusal; retain only bounded response diagnostics.

    The request ID is a correlation hint, never an authentication claim. Do not
    retain headers, endpoints, response text or arbitrary detail/reason strings.
    Diagnostics must not mask the original exception or initiate a retry.
    """
    try:
        governance._raise_gateway_error(response.status_code, payload)
    except PolicyTransportError as error:
        if getattr(error, "safe_code", None) in {
            "control_gateway_server_status", "control_timeout"
        }:
            with suppress(Exception):
                request_id = response.headers.get("X-Request-ID")
                if isinstance(request_id, str) and len(request_id) == 36:
                    error.request_id = str(UUID(request_id))
            with suppress(Exception):
                detail = extract_policy_detail(payload)
                code = None
                for key in ("code", "error", "error_kind"):
                    if key in detail:
                        code = detail[key]
                        break
                if isinstance(code, str) and code in CONTROL_GATEWAY_FAILURE_CODES:
                    error.gateway_failure_code = code
        raise


class ControlGatewayErrorMixin:
    def _raise_gateway_error(self, status_code: int, payload: Any) -> None:
        detail = extract_policy_detail(payload)
        if status_code == 504 and detail.get("code") == "native_preflight_timeout":
            error = PolicyTransportError("Native preflight timed out")
            error.safe_code = "control_timeout"
            raise error
        if status_code >= 500:
            error = PolicyTransportError("Remote control service is unavailable")
            error.safe_code = "control_gateway_server_status"
            error.http_status = status_code
            raise error
        # Authored policy envelopes must win over admission-looking codes.
        # Reason strings alone never confer goal-recovery authority.
        if status_code in {401, 403, 409, 429} and "violation_type" in detail:
            raise PolicyViolationError(
                "Action blocked by policy",
                str(detail.get("violation_type") or "policy_violation"),
                detail,
            )
        if (
            status_code == 429
            and detail.get("error") == "entitlement_denied"
            and detail.get("code") == "governed_action_decision_quota_exhausted"
        ):
            raise ControlAdmissionError(
                "The organization's governed action allowance is exhausted.",
                "governed_action_decision_quota_exhausted",
                {},
            )
        if status_code == 409 and detail.get("error") == "connection_required":
            raise ControlAdmissionError(
                "An active provider connection is required.",
                "connection_required",
                {"code": "connection_required"},
            )
        # An explicit policy envelope stays a policy refusal even when it
        # carries an admission-looking diagnostic code. Only the dedicated
        # admission envelope can acquire operational-error provenance.
        if (status_code in {401, 403}
                and detail.get("code") in _NATIVE_AUTHORITY_CODES
                and "violation_type" not in detail):
            raise NativeConnectionAuthorizationError(
                "Native connection authorization was rejected.", str(detail["code"]), {},
            )
        if status_code == 401:
            if detail.get("code") in _SAFE_GATEWAY_REMEDIATION_CODES:
                raise PolicyViolationError(
                    "Native connection authorization was rejected.",
                    str(detail["code"]),
                    {},
                )
            raise AuthenticationError("Controller authentication was rejected")
        if status_code == 403:
            native_code = native_rejection_code(detail)
            if native_code is not None:
                raise ControlAdmissionError(
                    NATIVE_REJECTION_MESSAGES[native_code], native_code, {}
                )
            message = (
                detail.get("message")
                or detail.get("error")
                or detail.get("detail")
                or "Action blocked by policy"
            )
            raise PolicyViolationError(
                str(message).strip() or "Action blocked by policy",
                (
                    str(detail.get("code"))
                    if detail.get("code") in _SAFE_GATEWAY_REMEDIATION_CODES
                    else str(detail.get("violation_type") or "policy_violation")
                ),
                detail,
            )
        # Never promote arbitrary 4xx response text into a local hook error.
        # In particular, request-validation failures must not be disguised as
        # an unexpected controller exception or a user sign-in problem.
        error = PolicyTransportError("Gateway rejected the controller request")
        error.safe_code = "control_gateway_client_status"
        raise error
