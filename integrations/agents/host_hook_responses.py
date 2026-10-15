# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Safe failure messages and host response encoding."""

import asyncio
from typing import Any
from .hook_control import HookControlError
from .host_hook_contracts import HookAdapterResponse
from .host_hook_events import _json
from .native_rejection_reasons import NATIVE_REJECTION_MESSAGES

_DENIED_REASON = "Atellagent policy denied this request."
_UNAVAILABLE_REASON = (
    "Atellagent local control could not complete the request; request denied."
)
_PRETOOL_DECISION_REASONS = {
    "native_hook_continuation_unsupported": (
        "Atellagent denied this tool because its policy requires a gate that native "
        "hooks cannot pause and resume. No approval is pending. Use a supported "
        "managed execution route."
    ),
    "semantic_action_review_timeout": (
        "Semantic review timed out. The tool was not executed. You may retry "
        "this unchanged tool call once; if it times out again, stop and report the failure."
    ),
    "control_timeout": (
        "Atellagent preflight timed out. The tool was not executed. You may retry "
        "this unchanged tool call once; if it times out again, stop and report the failure."
    ),
}
_SAFE_LOCAL_FAILURE_REASONS = {
    "hook_input_invalid": "Atellagent hook received invalid or incomplete host input; request denied.",
    "hook_adapter_failure": "Atellagent hook adapter could not process this event; request denied.",
    "local_host_authorization_required": (
        "Atellagent requires an approved connection and the device owner's consent. "
        "Review this connection's authorization in Atellagent before retrying."
    ),
    "local_host_authorization_expired": (
        "Atellagent connection authorization expired. Request a replacement "
        "authorization with administrator approval and owner consent before retrying."
    ),
    "local_host_authorization_revoked": (
        "Atellagent connection authorization was revoked. Signing in again does "
        "not restore it. Contact your administrator before retrying."
    ),
    "local_device_user_mismatch": (
        "Atellagent device and connection owners do not match. Check the selected "
        "connection; request denied."
    ),
    "device_binding_invalid": (
        "Atellagent could not verify this connection's device binding. Check its "
        "device and connection authorization; request denied."
    ),
    "device_inactive": (
        "Atellagent device authorization is inactive. Contact your administrator; "
        "request denied."
    ),
    "device_proof_invalid": (
        "Atellagent device proof could not be verified. Check the local device "
        "credentials and client version; request denied."
    ),
    "device_proof_expired": (
        "Atellagent device proof expired. Check the machine clock and connectivity; "
        "request denied."
    ),
    "device_proof_replayed": (
        "Atellagent rejected a reused device proof; request denied. Do not reuse "
        "a captured request."
    ),
    "governed_action_decision_quota_exhausted": "Atellagent governed action allowance is exhausted; request denied. Check your organization's Billing page.",
    "control_socket_missing": "Atellagent local control socket is unavailable; request denied.",
    "control_socket_access_denied": "Atellagent local control socket access was denied; request denied.",
    "control_socket_refused": "Atellagent local control service is not accepting requests; request denied.",
    "control_socket_unavailable": "Atellagent local control socket could not be reached; request denied.",
    "control_socket_timeout": "Atellagent local control service timed out; request denied.",
    "control_timeout": "Atellagent control service timed out; request denied.",
    "control_gateway_server_status": "Atellagent remote control service is unavailable; request denied.",
    "control_gateway_transport": "Atellagent remote control service could not be reached; request denied.",
    "control_gateway_auth": "Atellagent controller credentials were rejected; request denied. Check the local service status.",
    "control_gateway_client_status": "Atellagent gateway rejected this controller request. Check the local service status and client version before retrying.",
    "control_unavailable": "Atellagent local controller encountered an unexpected error. Check the local service status; the request was denied.",
    "model_decision_gateway_auth": "Atellagent control credentials were rejected; request denied.",
    "model_decision_gateway_client_status": "Atellagent control rejected the decision request; request denied.",
    "model_decision_gateway_response_invalid": "Atellagent control returned an invalid decision response; request denied.",
    "model_decision_gateway_server_status": "Atellagent control service failed while deciding the request; request denied.",
    "model_decision_capacity_exhausted": "Atellagent inspection service is busy; request denied. Please try again shortly.",
    "model_decision_gateway_status": "Atellagent control could not complete the decision request; request denied.",
    "model_decision_gateway_transport": "Atellagent control service could not be reached; request denied.",
    "model_decision_transport_failure": "Atellagent control could not obtain a decision; request denied.",
    "control_directive_timeout": "Atellagent could not verify the action authorization in time; request denied.",
    "control_directive_rejected": "Atellagent could not verify the action authorization; request denied.",
    "control_response_invalid": "Atellagent local control response was invalid; request denied.",
    "unknown_tool_call": (
        "Atellagent could not match this post-tool event to a pending preflight. "
        "The tool may already have executed; its outcome was not recorded. "
        "Do not repeat the tool solely because of this audit error."
    ),
    "outcome_binding_mismatch": (
        "Atellagent post-tool input does not match its admitted invocation. "
        "The tool may already have executed; its outcome was not recorded. "
        "Do not repeat the tool solely because of this audit error."
    ),
    "control_request_too_large": "Atellagent hook request exceeds the local size limit; request denied.",
    "unsupported_params_field": (
        "Atellagent hook and local controller versions differ; restart the local "
        "controller before retrying this tool."
    ),
    "control_runtime_already_running": "Atellagent local control runtime is already active; request denied.",
}


def _failure(exc: Exception | None = None) -> HookAdapterResponse:
    if isinstance(exc, asyncio.TimeoutError):
        return HookAdapterResponse(
            exit_code=2,
            stderr=_SAFE_LOCAL_FAILURE_REASONS["control_timeout"],
        )
    if isinstance(exc, HookControlError):
        code = exc.code
    else:
        code = "hook_adapter_failure"
    return HookAdapterResponse(
        exit_code=2,
        stderr=_SAFE_LOCAL_FAILURE_REASONS.get(
            code, NATIVE_REJECTION_MESSAGES.get(code, _UNAVAILABLE_REASON)
        ),
    )


def _prompt_result(host: str, allowed: bool) -> HookAdapterResponse:
    if allowed:
        return HookAdapterResponse(exit_code=0)
    return HookAdapterResponse(
        exit_code=0,
        stdout=_json({"decision": "block", "reason": _DENIED_REASON}),
    )


def _pretool_result(
    host: str, allowed: bool, reason_code: str | None = None
) -> HookAdapterResponse:
    reason = _PRETOOL_DECISION_REASONS.get(
        reason_code, NATIVE_REJECTION_MESSAGES.get(reason_code, _DENIED_REASON)
    )
    if host == "gemini-cli":
        if allowed:
            return HookAdapterResponse(exit_code=0)
        return HookAdapterResponse(
            exit_code=0,
            stdout=_json({"decision": "deny", "reason": reason}),
        )
    if allowed:
        # No rewrite is needed for ordinary allows.
        return HookAdapterResponse(exit_code=0)
    output: dict[str, Any] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
    }
    output["permissionDecisionReason"] = reason
    return HookAdapterResponse(
        exit_code=0,
        stdout=_json({"hookSpecificOutput": output}),
    )
