# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Canonical versioned ingress-envelope helpers for external runtime participation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional

from .agent_identity import normalize_identity_context
from .runtime_modes import normalize_runtime_mode


INGRESS_ENVELOPE_SCHEMA_VERSION = "v1"

_AUTHORITY_ONLY_CONTEXT_KEYS: tuple[str, ...] = (
    "execution_token",
    "execution_token_claims",
    "action_obligation",
    "action_obligation_claims",
)

_INGRESS_CONTEXT_KEYS: tuple[str, ...] = (
    "execution_id",
    "workflow_deployment_id",
    "run_id",
    "memory_thread_id",
    "channel_thread_id",
    "session_id",
    "session_cycle",
    "conversation_id",
    "trace_id",
    "parent_tool_use_id",
    "user_id",
    "tenant_id",
)


def _normalize_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def _normalize_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _normalize_capabilities(values: Iterable[Any] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = _normalize_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def normalize_ingress_context(payload: Mapping[str, Any] | None) -> Dict[str, Any]:
    source = _normalize_mapping(payload)
    authority_keys = sorted(
        key for key in _AUTHORITY_ONLY_CONTEXT_KEYS if source.get(key) not in (None, "", [], {})
    )
    if authority_keys:
        raise ValueError(
            "ingress context must not contain runtime authority fields: "
            + ", ".join(authority_keys)
        )
    normalized = {
        key: value
        for key in _INGRESS_CONTEXT_KEYS
        if (value := source.get(key)) not in (None, "", [], {})
    }
    identity_context = source.get("identity_context")
    if isinstance(identity_context, Mapping) and identity_context:
        normalized["identity_context"] = dict(identity_context)
    return normalize_identity_context(normalized)


@dataclass(frozen=True)
class RuntimeIngressEnvelope:
    kind: str
    runtime_mode: Optional[str] = None
    capabilities: list[str] = field(default_factory=list)
    identity_context: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    correlation_key: Optional[str] = None
    idempotency_key: Optional[str] = None
    adapter_payload: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = INGRESS_ENVELOPE_SCHEMA_VERSION

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        return {
            key: value
            for key, value in payload.items()
            if value not in (None, "", [], {})
        }


def build_ingress_envelope(
    *,
    kind: Any,
    runtime_mode: Any = None,
    capabilities: Iterable[Any] | None = None,
    identity_context: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    correlation_key: Any = None,
    idempotency_key: Any = None,
    adapter_payload: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    combined_context = normalize_ingress_context(
        {
            **_normalize_mapping(context),
            "identity_context": _normalize_mapping(identity_context),
        }
    )
    return RuntimeIngressEnvelope(
        kind=_normalize_text(kind) or "unknown",
        runtime_mode=normalize_runtime_mode(runtime_mode),
        capabilities=_normalize_capabilities(capabilities),
        identity_context=_normalize_mapping(combined_context.get("identity_context")),
        context=combined_context,
        correlation_key=_normalize_text(correlation_key),
        idempotency_key=_normalize_text(idempotency_key),
        adapter_payload=_normalize_mapping(adapter_payload),
    ).as_dict()


def ingress_envelope_from_mapping(payload: Mapping[str, Any] | None) -> Dict[str, Any]:
    source = _normalize_mapping(payload)
    return build_ingress_envelope(
        kind=source.get("kind"),
        runtime_mode=source.get("runtime_mode"),
        capabilities=source.get("capabilities"),
        identity_context=source.get("identity_context"),
        context=source.get("context"),
        correlation_key=source.get("correlation_key"),
        idempotency_key=source.get("idempotency_key"),
        adapter_payload=source.get("adapter_payload"),
    )


def supported_ingress_context_keys() -> list[str]:
    return list(_INGRESS_CONTEXT_KEYS)


__all__ = [
    "INGRESS_ENVELOPE_SCHEMA_VERSION",
    "RuntimeIngressEnvelope",
    "build_ingress_envelope",
    "ingress_envelope_from_mapping",
    "normalize_ingress_context",
    "supported_ingress_context_keys",
]
