# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Minimal versioned identity envelope for external runtime participation."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


IDENTITY_CONTEXT_KEY = "identity_context"
_IDENTITY_FLAT_KEYS = {
    "service_account_id",
    "agent_principal_id",
    "agent_principal_type",
    "identity_provider",
    "external_principal_id",
    "binding_id",
    "binding_type",
}


def _normalize_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _section(payload: Mapping[str, Any], key: str) -> Dict[str, Any]:
    return _mapping(payload.get(key))


def build_identity_envelope(
    *,
    service_account_id: Any = None,
    agent_principal_id: Any = None,
    agent_principal_type: Any = None,
    identity_provider: Any = None,
    external_principal_id: Any = None,
    binding_id: Any = None,
    binding_type: Any = None,
    authenticated_service_account_id: Any = None,
) -> Dict[str, Any]:
    """Build only the identity fields required by the public wire contract."""
    return {
        "executor_identity": {
            "service_account_id": _normalize_text(service_account_id)
            or _normalize_text(authenticated_service_account_id),
        },
        "principal_identity": {
            "agent_principal_id": _normalize_text(agent_principal_id),
            "agent_principal_type": _normalize_text(agent_principal_type),
        },
        "external_subject_identity": {
            "identity_provider": _normalize_text(identity_provider),
            "external_principal_id": _normalize_text(external_principal_id),
        },
        "binding_identity": {
            "binding_id": _normalize_text(binding_id),
            "binding_type": _normalize_text(binding_type),
        },
    }


def identity_envelope_from_mapping(
    payload: Mapping[str, Any] | None,
    *,
    authenticated_service_account_id: Any = None,
) -> Dict[str, Any]:
    source = _mapping(payload)
    flat_keys = sorted(key for key in _IDENTITY_FLAT_KEYS if key in source)
    if flat_keys:
        raise ValueError(
            "identity fields must use the nested identity_context envelope: "
            + ", ".join(flat_keys)
        )
    direct_envelope = any(
        key in source
        for key in (
            "executor_identity",
            "principal_identity",
            "external_subject_identity",
            "binding_identity",
        )
    )
    nested = _mapping(source.get(IDENTITY_CONTEXT_KEY))
    if direct_envelope and nested:
        raise ValueError("identity envelope must not be nested more than once")
    if direct_envelope:
        nested = source
    executor = _section(nested, "executor_identity")
    principal = _section(nested, "principal_identity")
    external = _section(nested, "external_subject_identity")
    binding = _section(nested, "binding_identity")
    return build_identity_envelope(
        service_account_id=executor.get("service_account_id"),
        agent_principal_id=principal.get("agent_principal_id"),
        agent_principal_type=principal.get("agent_principal_type"),
        identity_provider=external.get("identity_provider"),
        external_principal_id=external.get("external_principal_id"),
        binding_id=binding.get("binding_id"),
        binding_type=binding.get("binding_type"),
        authenticated_service_account_id=authenticated_service_account_id,
    )


def identity_envelope_to_flat_mapping(
    payload: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    identity = identity_envelope_from_mapping(payload)
    executor = _section(identity, "executor_identity")
    principal = _section(identity, "principal_identity")
    external = _section(identity, "external_subject_identity")
    binding = _section(identity, "binding_identity")
    values = {
        "service_account_id": executor.get("service_account_id"),
        "agent_principal_id": principal.get("agent_principal_id"),
        "agent_principal_type": principal.get("agent_principal_type"),
        "identity_provider": external.get("identity_provider"),
        "external_principal_id": external.get("external_principal_id"),
        "binding_id": binding.get("binding_id"),
        "binding_type": binding.get("binding_type"),
    }
    return {key: value for key, value in values.items() if value}


def normalize_identity_context(
    payload: Mapping[str, Any] | None,
    *,
    authenticated_service_account_id: Any = None,
) -> Dict[str, Any]:
    source = _mapping(payload)
    normalized = {key: value for key, value in source.items() if key != IDENTITY_CONTEXT_KEY}
    identity = identity_envelope_from_mapping(
        source,
        authenticated_service_account_id=authenticated_service_account_id,
    )
    if identity_envelope_to_flat_mapping(identity):
        normalized[IDENTITY_CONTEXT_KEY] = identity
    return normalized


def identity_envelope_violations(
    payload: Mapping[str, Any] | None,
    *,
    require_service_account: bool = False,
) -> list[str]:
    identity = identity_envelope_from_mapping(payload)
    executor = _section(identity, "executor_identity")
    principal = _section(identity, "principal_identity")
    external = _section(identity, "external_subject_identity")
    binding = _section(identity, "binding_identity")
    violations: list[str] = []
    if require_service_account and not _normalize_text(executor.get("service_account_id")):
        violations.append("missing_executor_identity")
    if bool(_normalize_text(principal.get("agent_principal_id"))) != bool(
        _normalize_text(principal.get("agent_principal_type"))
    ):
        violations.append("partial_principal_identity")
    if bool(_normalize_text(external.get("identity_provider"))) != bool(
        _normalize_text(external.get("external_principal_id"))
    ):
        violations.append("partial_external_subject_identity")
    if bool(_normalize_text(binding.get("binding_id"))) != bool(
        _normalize_text(binding.get("binding_type"))
    ):
        violations.append("partial_binding_identity")
    return violations


def has_bound_principal_identity(payload: Mapping[str, Any] | None) -> bool:
    identity = identity_envelope_from_mapping(payload)
    principal = _section(identity, "principal_identity")
    binding = _section(identity, "binding_identity")
    return bool(
        _normalize_text(principal.get("agent_principal_id"))
        and _normalize_text(principal.get("agent_principal_type"))
        and _normalize_text(binding.get("binding_id"))
        and _normalize_text(binding.get("binding_type"))
    )


__all__ = [
    "IDENTITY_CONTEXT_KEY",
    "build_identity_envelope",
    "has_bound_principal_identity",
    "identity_envelope_from_mapping",
    "identity_envelope_to_flat_mapping",
    "identity_envelope_violations",
    "normalize_identity_context",
]
