# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Portable workflow context shared by public SDK and proxy integrations.

This context is the customer-visible execution envelope.  It intentionally
excludes execution credentials, decoded authority claims, and action
obligations; those stay in the authenticated, request-scoped SDK transport.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Dict, Mapping, Optional

from .agent_identity import identity_envelope_from_mapping, identity_envelope_violations


_PORTABLE_KEYS = (
    "execution_id", "workflow_deployment_id", "run_id", "memory_thread_id",
    "channel_thread_id", "session_id", "session_cycle", "conversation_id",
    "trace_id", "parent_tool_use_id", "user_id", "tenant_id",
)
_LEGACY_IDENTITY_KEYS = {
    "service_account_id", "agent_principal_id", "agent_principal_type",
    "identity_provider", "external_principal_id", "binding_id", "binding_type",
}
_AUTHORITY_ONLY_KEYS = (
    "execution_token", "execution_token_claims", "action_obligation",
    "action_obligation_claims",
)
_AUTHORITY_HEADERS = {
    "x-workflow-execution-token", "x-execution-token", "x-atellagent-action-obligation",
}
_HEADER_CONTEXT_KEYS = (
    ("user_id", "X-Workflow-User-Id"),
    ("tenant_id", "X-Workflow-Tenant-Id"),
)
_IDENTITY_HEADER_CONTEXT_KEYS = (
    ("agent_principal_id", "X-Atellagent-Agent-Principal-Id"),
    ("identity_provider", "X-Atellagent-Agent-Identity-Provider"),
    ("external_principal_id", "X-Atellagent-Agent-External-Principal-Id"),
    ("binding_id", "X-Atellagent-Agent-Binding-Id"),
)
_workflow_context_var: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "atellagent_workflow_context", default=None
)


def _value(source: Mapping[str, Any] | None, key: str) -> Any:
    if not isinstance(source, Mapping):
        return None
    value = source.get(key)
    return None if value in (None, "", [], {}) else value


def _public_identity(context: Mapping[str, Any] | None) -> Optional[Dict[str, Any]]:
    identity = identity_envelope_from_mapping(context)
    projections = (
        ("executor_identity", ("service_account_id",)),
        ("principal_identity", ("agent_principal_id", "agent_principal_type")),
        ("external_subject_identity", ("identity_provider", "external_principal_id")),
        ("binding_identity", ("binding_id", "binding_type")),
    )
    result: Dict[str, Any] = {}
    for name, keys in projections:
        candidate = identity.get(name)
        values = {key: value for key in keys if (value := _value(candidate, key)) is not None}
        if values:
            result[name] = values
    return result or None


def _identity_header_values(context: Mapping[str, Any] | None) -> Dict[str, Any]:
    identity = identity_envelope_from_mapping(context)
    sources = {
        "agent_principal_id": ("principal_identity", "agent_principal_id"),
        "identity_provider": ("external_subject_identity", "identity_provider"),
        "external_principal_id": ("external_subject_identity", "external_principal_id"),
        "binding_id": ("binding_identity", "binding_id"),
    }
    return {
        output: value
        for output, (container, key) in sources.items()
        if (value := _value(identity.get(container), key)) is not None
    }


def strip_runtime_authority_headers(headers: Mapping[str, str]) -> Dict[str, str]:
    """Ensure caller-provided headers can never grant runtime authority."""
    return {
        key: value
        for key, value in dict(headers).items()
        if str(key).lower() not in _AUTHORITY_HEADERS
    }


def normalize_portable_workflow_context(
    context: Mapping[str, Any] | None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(context, Mapping):
        return None
    authority_keys = sorted(key for key in _AUTHORITY_ONLY_KEYS if _value(context, key))
    if authority_keys:
        raise ValueError("workflow_context must not contain runtime authority fields: " + ", ".join(authority_keys))
    legacy_keys = sorted(key for key in _LEGACY_IDENTITY_KEYS if key in context)
    if legacy_keys:
        raise ValueError("workflow_context must use identity_context instead of flat identity fields: " + ", ".join(legacy_keys))
    result = {key: value for key in _PORTABLE_KEYS if (value := _value(context, key)) is not None}
    if identity := _public_identity(context):
        result["identity_context"] = identity
    violations = identity_envelope_violations(result)
    if violations:
        raise ValueError("workflow_context has invalid identity envelope: " + ", ".join(violations))
    return result or None


def extract_portable_workflow_context(payload: Mapping[str, Any] | None) -> Optional[Dict[str, Any]]:
    return normalize_portable_workflow_context(payload.get("workflow_context")) if isinstance(payload, Mapping) else None


def merge_portable_workflow_context(
    primary: Mapping[str, Any] | None,
    fallback: Mapping[str, Any] | None,
) -> Optional[Dict[str, Any]]:
    first = normalize_portable_workflow_context(primary) or {}
    second = normalize_portable_workflow_context(fallback) or {}
    merged = {key: first.get(key, second.get(key)) for key in _PORTABLE_KEYS if first.get(key, second.get(key)) is not None}
    if identity := first.get("identity_context") or second.get("identity_context"):
        merged["identity_context"] = identity
    return normalize_portable_workflow_context(merged)


def serialize_portable_workflow_context(context: Mapping[str, Any] | None) -> Optional[Dict[str, Any]]:
    return normalize_portable_workflow_context(context)


def apply_workflow_headers(
    headers: Mapping[str, str], *, workflow_context: Mapping[str, Any] | None
) -> Dict[str, str]:
    augmented = strip_runtime_authority_headers(headers)
    context = normalize_portable_workflow_context(workflow_context)
    if not context:
        return augmented
    for source_key, header_key in _HEADER_CONTEXT_KEYS:
        if value := context.get(source_key):
            augmented[header_key] = str(value)
    for source_key, header_key in _IDENTITY_HEADER_CONTEXT_KEYS:
        if value := _identity_header_values(context).get(source_key):
            augmented[header_key] = str(value)
    return augmented


def set_workflow_context(context: Mapping[str, Any] | None):
    return _workflow_context_var.set(normalize_portable_workflow_context(context))


def reset_workflow_context(token: Any) -> None:
    if token is not None:
        _workflow_context_var.reset(token)


def get_workflow_context() -> Optional[Dict[str, Any]]:
    return _workflow_context_var.get()


__all__ = [
    "apply_workflow_headers", "extract_portable_workflow_context", "get_workflow_context",
    "merge_portable_workflow_context", "normalize_portable_workflow_context",
    "reset_workflow_context", "serialize_portable_workflow_context", "set_workflow_context",
    "strip_runtime_authority_headers",
]
