# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Validation and coercion for the v1 workflow participant protocol."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from atellagent_client.protocol.context import normalize_portable_workflow_context

from .types import (
    WORKFLOW_PARTICIPANT_PROTOCOL_VERSION,
    WorkflowParticipantCancelRequest,
    WorkflowParticipantCompileRequest,
    WorkflowParticipantExecutionRequest,
    WorkflowParticipantResumeRequest,
)


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _protocol_version(payload: Mapping[str, Any]) -> str:
    version = _text(payload.get("protocol_version"))
    if version != WORKFLOW_PARTICIPANT_PROTOCOL_VERSION:
        raise ValueError(
            "workflow participant protocol_version must be "
            f"'{WORKFLOW_PARTICIPANT_PROTOCOL_VERSION}'"
        )
    return version


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = _text(payload.get(key))
    if not value:
        raise ValueError(f"workflow participant request requires {key}")
    return value


def _reject_unknown(payload: Mapping[str, Any], allowed: set[str]) -> None:
    unsupported = sorted(set(payload) - allowed)
    if unsupported:
        raise ValueError(
            "workflow participant request contains unsupported fields: "
            + ", ".join(unsupported)
        )


def _public_context(payload: Mapping[str, Any]) -> Dict[str, Any]:
    context = normalize_portable_workflow_context(_mapping(payload.get("workflow_context")))
    return context or {}


def coerce_workflow_participant_compile_request(
    payload: Mapping[str, Any] | None,
) -> WorkflowParticipantCompileRequest:
    source = _mapping(payload)
    _reject_unknown(
        source,
        {
            "protocol_version",
            "tenant_id",
            "deployment_id",
            "runtime_type",
            "workflow_definition",
            "runtime_configuration",
        },
    )
    return {
        "protocol_version": _protocol_version(source),
        "tenant_id": _required_text(source, "tenant_id"),
        "deployment_id": _required_text(source, "deployment_id"),
        "runtime_type": _required_text(source, "runtime_type"),
        "workflow_definition": _mapping(source.get("workflow_definition")),
        "runtime_configuration": _mapping(source.get("runtime_configuration")),
    }


def _coerce_execution(
    payload: Mapping[str, Any] | None,
    *,
    operation: str,
) -> WorkflowParticipantExecutionRequest:
    source = _mapping(payload)
    _reject_unknown(
        source,
        {
            "protocol_version",
            "operation",
            "execution_id",
            "deployment_id",
            "attempt_id",
            "request_id",
            "runtime_input",
            "workflow_context",
            "continuation",
            "wake_input",
        },
    )
    if _text(source.get("operation")) != operation:
        raise ValueError(f"workflow participant operation must be '{operation}'")
    return {
        "protocol_version": _protocol_version(source),
        "operation": operation,
        "execution_id": _required_text(source, "execution_id"),
        "deployment_id": _required_text(source, "deployment_id"),
        "attempt_id": _required_text(source, "attempt_id"),
        "request_id": _required_text(source, "request_id"),
        "runtime_input": _mapping(source.get("runtime_input")),
        "workflow_context": _public_context(source),
    }


def coerce_workflow_participant_execute_request(
    payload: Mapping[str, Any] | None,
) -> WorkflowParticipantExecutionRequest:
    return _coerce_execution(payload, operation="execute")


def coerce_workflow_participant_resume_request(
    payload: Mapping[str, Any] | None,
) -> WorkflowParticipantResumeRequest:
    source = _mapping(payload)
    coerced: WorkflowParticipantResumeRequest = dict(
        _coerce_execution(source, operation="resume")
    )
    coerced["continuation"] = _mapping(source.get("continuation"))
    coerced["wake_input"] = _mapping(source.get("wake_input"))
    return coerced


def coerce_workflow_participant_cancel_request(
    payload: Mapping[str, Any] | None,
) -> WorkflowParticipantCancelRequest:
    source = _mapping(payload)
    _reject_unknown(
        source,
        {
            "protocol_version",
            "operation",
            "tenant_id",
            "execution_id",
            "attempt_id",
            "reason",
        },
    )
    if _text(source.get("operation")) != "cancel":
        raise ValueError("workflow participant operation must be 'cancel'")
    return {
        "protocol_version": _protocol_version(source),
        "operation": "cancel",
        "tenant_id": _required_text(source, "tenant_id"),
        "execution_id": _required_text(source, "execution_id"),
        "attempt_id": _required_text(source, "attempt_id"),
        "reason": _text(source.get("reason")) or "cancelled",
    }


__all__ = [
    "coerce_workflow_participant_cancel_request",
    "coerce_workflow_participant_compile_request",
    "coerce_workflow_participant_execute_request",
    "coerce_workflow_participant_resume_request",
]
