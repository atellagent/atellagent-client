# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bounded validation, diagnostics, and correlation for local hook RPCs."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import re
from uuid import UUID
from typing import Any, Dict, Mapping, Optional

import httpx

from atellagent_client.governance import ActionDenied
from atellagent_client.sdk.errors import AuthenticationError, PolicyTransportError, PolicyViolationError

from .hook_control_protocol import HookControlError
from .control_gateway_errors import CONTROL_GATEWAY_FAILURE_CODES
from atellagent_client.onboarding.errors import OnboardingClientError

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_MODEL_DECISION_FAILURE_CODES = frozenset(
    {
        "model_decision_gateway_auth",
        "model_decision_gateway_client_status",
        "model_decision_gateway_response_invalid",
        "model_decision_gateway_server_status",
        "model_decision_gateway_status",
        "model_decision_gateway_transport",
        "model_decision_capacity_exhausted",
    }
)


def _identifier(value: Any, field_name: str) -> str:
    candidate = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(candidate):
        raise HookControlError(f"invalid_{field_name}")
    return candidate


def _object(value: Any, field_name: str, *, allowed: set[str]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HookControlError(f"invalid_{field_name}")
    result = dict(value)
    if set(result) - allowed:
        raise HookControlError(f"unsupported_{field_name}_field")
    return result


def _messages(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise HookControlError("invalid_messages")
    messages: list[Dict[str, Any]] = []
    for message in value:
        if not isinstance(message, Mapping):
            raise HookControlError("invalid_messages")
        normalized = dict(message)
        role = normalized.get("role")
        if role not in {"system", "user", "assistant", "tool", "function"}:
            raise HookControlError("invalid_messages")
        if not isinstance(normalized.get("content"), str):
            raise HookControlError("invalid_messages")
        messages.append(normalized)
    return messages


def _safe_error_code(exc: Exception, *, default: str = "control_unavailable") -> str:
    if isinstance(exc, HookControlError):
        return exc.code
    if isinstance(exc, PolicyViolationError):
        return str(exc.violation_type or "policy_denied")
    if isinstance(exc, ActionDenied):
        return exc.reason_code
    if isinstance(exc, OnboardingClientError):
        return exc.code
    if isinstance(exc, AuthenticationError):
        return "control_gateway_auth"
    if isinstance(exc, httpx.TransportError):
        return "control_gateway_transport"
    if isinstance(exc, PolicyTransportError):
        safe_code = getattr(exc, "safe_code", None)
        if safe_code in {
            "control_gateway_client_status", "control_gateway_server_status", "control_timeout"
        }:
            return safe_code
        if safe_code in _MODEL_DECISION_FAILURE_CODES:
            return safe_code
        return "model_decision_transport_failure"
    return default


def _safe_outcome_delivery_error_code(exc: Exception) -> str:
    """Classify retry failures without retaining endpoint responses or action data."""

    if isinstance(exc, asyncio.TimeoutError):
        return "outcome_delivery_timeout"
    if isinstance(exc, PolicyViolationError):
        if exc.violation_type == "native_hook_outcome_receipt_expired":
            return "outcome_delivery_receipt_expired"
        return "outcome_delivery_policy_rejected"
    if isinstance(exc, PolicyTransportError):
        if getattr(exc, "safe_code", None) == "control_gateway_server_status":
            return "outcome_delivery_gateway_server_status"
        return "outcome_delivery_transport"
    if isinstance(exc, httpx.TransportError):
        return "outcome_delivery_transport"
    return "outcome_delivery_gateway_client_status"


def _safe_http_status(exc: Exception) -> int | None:
    """Expose only a bounded HTTP status, never a remote error body."""

    status = getattr(exc, "http_status", None)
    return status if type(status) is int and 500 <= status <= 599 else None


def _safe_gateway_failure_code(exc: Exception) -> str:
    """A fixed dependency label for logs, never a host refusal or verdict."""

    if not isinstance(exc, PolicyTransportError) or getattr(exc, "safe_code", None) not in {
        "control_gateway_server_status", "control_timeout"
    }:
        return "unavailable"
    code = getattr(exc, "gateway_failure_code", None)
    return code if isinstance(code, str) and code in CONTROL_GATEWAY_FAILURE_CODES else "unavailable"


def _safe_request_id(exc: Exception) -> str:
    value = getattr(exc, "request_id", None)
    if not isinstance(value, str):
        return "unavailable"
    try:
        return str(UUID(value))
    except ValueError:
        return "unavailable"


def _controller_diagnostic_code(exc: Exception) -> Optional[str]:
    """Return one bounded remediation state suitable for health output."""

    code = _safe_error_code(exc)
    return {
        "local_host_authorization_required": "native_delegation_required",
        "local_host_authorization_revoked": "user_grant_revoked",
        "local_host_authorization_expired": "user_grant_expired",
        "local_device_user_mismatch": "native_device_owner_mismatch",
        "device_binding_invalid": "native_device_binding_invalid",
        "device_inactive": "native_device_inactive",
        "device_proof_invalid": "native_device_proof_invalid",
        "device_proof_expired": "native_device_proof_expired",
        "device_proof_replayed": "native_device_proof_replayed",
        "connection_required": "provider_connection_required",
        "native_hook_outcome_receipt_expired": "outcome_receipt_expired",
    }.get(code)


def _request_correlation(params: Any) -> str:
    """Correlate failures without logging host content or raw identifiers."""
    if not isinstance(params, Mapping):
        return "unavailable"
    fields = [params.get(key) for key in ("host", "session_id", "turn_id", "tool_call_id")]
    if all(isinstance(value, str) and value for value in fields):
        return sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()[:24]
    if ("messages" in params
            and params.get("input_scope", "turn_entry") in {"turn_entry", "full_model_request"}
            and all(isinstance(value, str) and value for value in fields[:3])):
        return sha256(json.dumps(["model", *fields[:3]], separators=(",", ":")).encode()).hexdigest()[:24]
    # MCP bridge calls intentionally do not receive host-session-turn context.
    # Their tool name and host-issued call ID are sufficient to correlate a
    # failure without retaining arguments or exposing either identifier.
    mcp_fields = [params.get(key) for key in ("tool_name", "tool_call_id")]
    if all(isinstance(value, str) and value for value in mcp_fields):
        return sha256(
            json.dumps(["mcp", *mcp_fields], separators=(",", ":")).encode()
        ).hexdigest()[:24]
    return "unavailable"
