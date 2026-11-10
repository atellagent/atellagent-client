# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Contracts for governed callable-agent boundary execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

_BOUNDARY_CONTROL_KEYS = ("__atellagent", "_atellagent")


def _normalize_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


@dataclass(frozen=True)
class BoundaryExecutionMetadata:
    policy_metadata: Dict[str, Any] = field(default_factory=dict)
    intent: Dict[str, Any] = field(default_factory=dict)
    resource: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    pep_facts: Dict[str, Any] = field(default_factory=dict)
    error_category: Optional[str] = None


@dataclass(frozen=True)
class BoundaryToolCall:
    request_id: Any
    method: Optional[str]
    tool_name: Optional[str]
    tool_call_id: Optional[str]
    sanitized_payload: Dict[str, Any]
    arguments: Dict[str, Any]
    metadata: BoundaryExecutionMetadata


@dataclass(frozen=True)
class BoundaryExecutionOutcome:
    success: bool
    result_payload: Any
    error_message: Optional[str] = None
    error_category: Optional[str] = None
    resource: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)


def _extract_control_payload(arguments: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    sanitized_arguments = dict(arguments or {})
    control_payload: Dict[str, Any] = {}
    for key in _BOUNDARY_CONTROL_KEYS:
        candidate = sanitized_arguments.pop(key, None)
        if isinstance(candidate, dict):
            control_payload = dict(candidate)
            break
    return sanitized_arguments, control_payload


def _sanitize_payload_arguments(payload: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = dict(payload or {})
    params = sanitized.get("params")
    if isinstance(params, dict):
        sanitized["params"] = {
            **params,
            "arguments": dict(arguments),
        }
    return sanitized


def extract_boundary_tool_call(payload: Dict[str, Any]) -> BoundaryToolCall:
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    raw_arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    arguments, control_payload = _extract_control_payload(raw_arguments)
    request_id = payload.get("id") if isinstance(payload, dict) else None
    method = _normalize_optional_text(payload.get("method")) if isinstance(payload, dict) else None
    tool_name = _normalize_optional_text(params.get("name"))
    tool_call_id = _normalize_optional_text(payload.get("id"))
    metadata = BoundaryExecutionMetadata(
        policy_metadata=_coerce_dict(control_payload.get("policy_metadata")),
        intent=_coerce_dict(control_payload.get("intent")),
        resource=_coerce_dict(control_payload.get("resource")),
        evidence=_coerce_dict(control_payload.get("evidence")),
        pep_facts=_coerce_dict(control_payload.get("pep_facts")),
        error_category=_normalize_optional_text(control_payload.get("error_category")),
    )
    return BoundaryToolCall(
        request_id=request_id,
        method=method,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        sanitized_payload=_sanitize_payload_arguments(payload, arguments),
        arguments=arguments,
        metadata=metadata,
    )


def _extract_result_metadata(payload: Any) -> BoundaryExecutionMetadata:
    candidates: list[Dict[str, Any]] = []
    if isinstance(payload, dict):
        candidates.append(payload)
        result = payload.get("result")
        if isinstance(result, dict):
            candidates.append(result)
            structured = result.get("structuredContent")
            if isinstance(structured, dict):
                candidates.append(structured)

    metadata: Dict[str, Any] = {}
    for candidate in candidates:
        direct = candidate.get("atellagent")
        if isinstance(direct, dict):
            metadata = dict(direct)
            break
        for key in _BOUNDARY_CONTROL_KEYS:
            alternate = candidate.get(key)
            if isinstance(alternate, dict):
                metadata = dict(alternate)
                break
        if metadata:
            break

    error_category = _normalize_optional_text(metadata.get("error_category"))
    if not error_category and isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            error_category = _normalize_optional_text(
                error.get("category")
                or (error.get("details") or {}).get("category")
                if isinstance(error.get("details"), dict)
                else None
            )

    return BoundaryExecutionMetadata(
        policy_metadata=_coerce_dict(metadata.get("policy_metadata")),
        intent=_coerce_dict(metadata.get("intent")),
        resource=_coerce_dict(metadata.get("resource")),
        evidence=_coerce_dict(metadata.get("evidence")),
        pep_facts={},
        error_category=error_category,
    )


def _extract_error_message(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return _normalize_optional_text(payload)
    error = payload.get("error")
    if isinstance(error, dict):
        return _normalize_optional_text(
            error.get("message") or error.get("detail") or error.get("error")
        )
    return _normalize_optional_text(payload.get("detail"))


def normalize_boundary_execution_outcome(result_payload: Any) -> BoundaryExecutionOutcome:
    metadata = _extract_result_metadata(result_payload)
    success = not (
        isinstance(result_payload, dict) and isinstance(result_payload.get("error"), dict)
    )
    return BoundaryExecutionOutcome(
        success=success,
        result_payload=result_payload if success else None,
        error_message=None if success else _extract_error_message(result_payload),
        error_category=metadata.error_category if not success else metadata.error_category,
        resource=metadata.resource,
        evidence=metadata.evidence,
    )


__all__ = [
    "BoundaryExecutionMetadata",
    "BoundaryExecutionOutcome",
    "BoundaryToolCall",
    "extract_boundary_tool_call",
    "normalize_boundary_execution_outcome",
]
