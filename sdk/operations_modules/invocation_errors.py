# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""SDK error payload helpers for model invocation operations."""

from __future__ import annotations

from typing import Any, Optional

from atellagent_client.sdk.errors import PolicyViolationError
from .common import extract_policy_detail


def error_kind_from_payload(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    for candidate in (
        payload.get("error_kind"),
        (payload.get("detail") or {}).get("error_kind")
        if isinstance(payload.get("detail"), dict)
        else None,
        (payload.get("error") or {}).get("error_kind")
        if isinstance(payload.get("error"), dict)
        else None,
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def error_message_from_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(detail, dict):
            for key in ("message", "error", "detail", "reason"):
                value = detail.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()
        if isinstance(error, dict):
            for key in ("message", "error", "detail", "reason"):
                value = error.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    return "Request forbidden"


def is_policy_403_payload(payload: Any) -> bool:
    detail = extract_policy_detail(payload)
    if not isinstance(detail, dict):
        return False
    if detail.get("violation_type"):
        return True
    if detail.get("error_kind") == "policy_denied":
        return True
    error_value = detail.get("error")
    return isinstance(error_value, str) and "policy" in error_value.lower()


def raise_forbidden_invocation_error(
    payload: Any,
    *,
    request_id: Optional[str] = None,
) -> None:
    detail = extract_policy_detail(payload)
    if is_policy_403_payload(payload):
        violation_type = (
            detail.get("violation_type")
            if isinstance(detail, dict)
            else "unknown"
        ) or "unknown"
        message = (
            detail.get("message")
            if isinstance(detail, dict) and isinstance(detail.get("message"), str)
            else "Request blocked by policy"
        )
        raise PolicyViolationError(
            str(message),
            str(violation_type),
            detail if isinstance(detail, dict) else {},
        )

    error_kind = error_kind_from_payload(payload)
    message = error_message_from_payload(payload)
    if request_id:
        if error_kind:
            raise RuntimeError(
                f"Model invocation forbidden (request_id={request_id}, error_kind={error_kind}): {message}"
            )
        raise RuntimeError(
            f"Model invocation forbidden (request_id={request_id}): {message}"
        )
    if error_kind:
        raise RuntimeError(
            f"Model invocation forbidden (error_kind={error_kind}): {message}"
        )
    raise RuntimeError(f"Model invocation forbidden: {message}")
