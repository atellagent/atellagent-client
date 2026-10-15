# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Versioned, vendor-neutral PEP contracts for local and connected control."""

from __future__ import annotations

import logging
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Any, Callable, Dict, Iterable, Literal, Mapping, Optional

import jwt

Mode = Literal["observe", "enforce"]
ControlSource = Literal["cluster_directive", "local_manifest"]
MANIFEST_VERSION = "v1"
DIRECTIVE_VERSION = "v1"
_CONTROL_SOURCES = frozenset({"cluster_directive", "local_manifest"})
_INTEGRATION_KINDS = frozenset(
    {"tool", "mcp", "agent", "channel", "workflow", "model", "filter"}
)
_LOCAL_KINDS = frozenset({"tool", "mcp"})
_RULE_KEYS = frozenset(
    {
        "readable_roots",
        "writable_roots",
        "max_bytes",
        "max_results",
        "max_patch_hunks",
        "allowed_profiles",
        "allowed_git_targets",
    }
)
_LOGGER = logging.getLogger("atellagent_client.pep")
DecisionObserver = Callable[["DecisionEnvelope"], None]


class DirectiveValidationError(ValueError):
    """Raised when an opaque remote control directive is unusable."""


def raw_action_fingerprint(
    *, action: str, facts: Dict[str, Any],
    raw_context: Optional[Mapping[str, Any]] = None,
) -> str:
    """Return the exact invocation binding carried by a signed directive."""

    payload = {"tool_name": action.strip(), "arguments": dict(facts)}
    if raw_context:
        payload["raw_context"] = dict(raw_context)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _text(value: Any, field_name: str, *, required: bool = False) -> Optional[str]:
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    result = str(value).strip()
    if required and not result:
        raise ValueError(f"{field_name} is required")
    return result or None


def _mode(value: Any, field_name: str = "mode") -> Mode:
    result = _text(value, field_name, required=True)
    if result not in {"observe", "enforce"}:
        raise ValueError(f"{field_name} must be 'observe' or 'enforce'")
    return result  # type: ignore[return-value]


def _strings(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    result = tuple(item for item in (_text(item, field_name) for item in value) if item)
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


def _positive_int(value: Any, field_name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if result <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return result


def _under_roots(raw_path: str, roots: Iterable[str]) -> bool:
    candidate = Path(raw_path).expanduser().resolve(strict=False)
    return any(
        candidate.is_relative_to(Path(root).expanduser().resolve(strict=False))
        for root in roots
    )


@dataclass(frozen=True)
class IntegrationCapability:
    """Declares an adapter boundary; it is not an identity assertion."""

    integration_type: str
    intercepts_actions: bool
    action_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.integration_type not in _INTEGRATION_KINDS:
            raise ValueError("integration_type is unsupported")

    @property
    def supports_local_guardrails(self) -> bool:
        return self.intercepts_actions and self.integration_type in _LOCAL_KINDS


@dataclass(frozen=True)
class ActionIntent:
    action: str
    capability: IntegrationCapability
    correlation_id: str
    facts: Dict[str, Any] = field(default_factory=dict)
    tenant_id: Optional[str] = None
    execution_id: Optional[str] = None
    workspace_id: Optional[str] = None
    evidence_references: tuple[str, ...] = ()
    raw_action_fingerprint: Optional[str] = None
    policy_decision_id: Optional[str] = None
    raw_context: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text(self.action, "action", required=True)
        _text(self.correlation_id, "correlation_id", required=True)
        if any(
            not isinstance(reference, str) or not reference.strip()
            for reference in self.evidence_references
        ):
            raise ValueError("evidence_references must contain non-empty strings")
        expected_fingerprint = raw_action_fingerprint(
            action=self.action, facts=self.facts, raw_context=self.raw_context,
        )
        if (
            self.raw_action_fingerprint is not None
            and self.raw_action_fingerprint != expected_fingerprint
        ):
            raise ValueError("raw_action_fingerprint does not match action facts")
        object.__setattr__(self, "raw_action_fingerprint", expected_fingerprint)
        if self.policy_decision_id is not None:
            _text(self.policy_decision_id, "policy_decision_id", required=True)

    def to_remote_request(self) -> Dict[str, Any]:
        """Serialize only the stable public inputs to a remote control service."""
        return {
            "schema_version": DIRECTIVE_VERSION,
            "action": self.action,
            "integration_type": self.capability.integration_type,
            "technical_capabilities": list(self.capability.action_kinds),
            "correlation_id": self.correlation_id,
            "facts": dict(self.facts),
            "raw_action_fingerprint": self.raw_action_fingerprint,
            "policy_decision_id": self.policy_decision_id,
            "tenant_id": self.tenant_id,
            "execution_id": self.execution_id,
            "workspace_id": self.workspace_id,
            "evidence_references": list(self.evidence_references),
        }


@dataclass(frozen=True)
class _ActionRule:
    readable_roots: tuple[str, ...] = ()
    writable_roots: tuple[str, ...] = ()
    max_bytes: Optional[int] = None
    max_results: Optional[int] = None
    max_patch_hunks: Optional[int] = None
    allowed_profiles: tuple[str, ...] = ()
    allowed_git_targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class LocalGuardrailManifest:
    mode: Mode
    actions: Dict[str, _ActionRule]
    schema_version: str = MANIFEST_VERSION


@dataclass(frozen=True)
class RemoteControlDirective:
    mode: Mode
    allowed: bool
    action: str
    integration_type: str
    audience: str
    expires_at: int
    directive_id: str
    tenant_id: Optional[str] = None
    execution_id: Optional[str] = None
    workspace_id: Optional[str] = None
    evidence_requirements: tuple[str, ...] = ()
    reason_code: Optional[str] = None


@dataclass(frozen=True)
class DecisionEnvelope:
    source: Literal["local", "remote"]
    mode: Mode
    decision: Literal["allow", "deny"]
    would_enforce: bool
    action: str
    correlation_id: str
    coverage: Literal["covered", "unsupported", "unavailable"]
    reason_code: Optional[str] = None
    evidence_requirements: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()




def load_local_guardrail_manifest(path: str) -> LocalGuardrailManifest:
    from .local_guardrails import load_local_guardrail_manifest as load

    return load(path)


def _local_violation(
    intent: ActionIntent, manifest: LocalGuardrailManifest
) -> Optional[str]:
    from .local_guardrails import local_violation

    return local_violation(intent, manifest)


def _local_decision(
    intent: ActionIntent, manifest: LocalGuardrailManifest
) -> DecisionEnvelope:
    from .local_guardrails import local_decision

    return local_decision(intent, manifest)


class RemoteDirectiveVerifier:
    """Validates opaque signed directives without evaluating remote policy."""

    def __init__(
        self,
        key_resolver: Callable[[str], Any],
        *,
        audience: str,
        issuer: str = "gateway",
        is_revoked: Optional[Callable[[str], bool]] = None,
        now: Callable[[], float] = time,
    ) -> None:
        self._key_resolver, self._audience, self._issuer, self._now = (
            key_resolver,
            audience,
            _text(issuer, "directive issuer", required=True),
            now,
        )
        self._is_revoked = is_revoked or (lambda _directive_id: False)
        self._seen: set[str] = set()

    def verify(self, encoded: str, intent: ActionIntent) -> RemoteControlDirective:
        try:
            header = jwt.get_unverified_header(encoded)
            key_id = _text(header.get("kid"), "directive kid", required=True)
            assert key_id is not None
            claims = jwt.decode(
                encoded,
                self._key_resolver(key_id),
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["exp", "aud", "iss", "jti"],
                    "verify_exp": False,
                },
            )
        except Exception as exc:
            raise DirectiveValidationError("remote_directive_invalid") from exc
        return directive_from_verified_claims(
            claims,
            intent,
            audience=self._audience,
            issuer=self._issuer,
            is_revoked=self._is_revoked,
            seen=self._seen,
            now=self._now,
        )


def directive_from_verified_claims(
    claims: Dict[str, Any],
    intent: ActionIntent,
    *,
    audience: str,
    issuer: str = "gateway",
    is_revoked: Optional[Callable[[str], bool]] = None,
    seen: Optional[set[str]] = None,
    now: Callable[[], float] = time,
) -> RemoteControlDirective:
    from .directive_claims import directive_from_verified_claims as validate

    return validate(
        claims, intent, audience=audience, issuer=issuer,
        is_revoked=is_revoked, seen=seen, now=now,
    )


def evaluate_action(
    intent: ActionIntent,
    *,
    local_manifest: Optional[LocalGuardrailManifest] = None,
    remote_directive: Optional[RemoteControlDirective] = None,
    remote_required: bool = False,
    decision_observer: Optional[DecisionObserver] = None,
) -> DecisionEnvelope:
    from .evaluation import evaluate_action as evaluate

    return evaluate(
        intent, local_manifest=local_manifest, remote_directive=remote_directive,
        remote_required=remote_required, decision_observer=decision_observer,
    )


def evaluate_connected_action(
    intent: ActionIntent,
    *,
    verifier: RemoteDirectiveVerifier,
    encoded_directive: str,
    local_manifest: Optional[LocalGuardrailManifest] = None,
    decision_observer: Optional[DecisionObserver] = None,
) -> DecisionEnvelope:
    from .evaluation import evaluate_connected_action as evaluate

    return evaluate(
        intent, verifier=verifier, encoded_directive=encoded_directive,
        local_manifest=local_manifest, decision_observer=decision_observer,
    )


def evaluate_controlled_action(
    intent: ActionIntent,
    *,
    source: ControlSource = "cluster_directive",
    verifier: Optional[RemoteDirectiveVerifier] = None,
    encoded_directive: Optional[str] = None,
    local_manifest: Optional[LocalGuardrailManifest] = None,
    decision_observer: Optional[DecisionObserver] = None,
) -> DecisionEnvelope:
    from .evaluation import evaluate_controlled_action as evaluate

    return evaluate(
        intent, source=source, verifier=verifier, encoded_directive=encoded_directive,
        local_manifest=local_manifest, decision_observer=decision_observer,
    )
