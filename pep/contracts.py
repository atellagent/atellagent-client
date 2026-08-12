# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Versioned, vendor-neutral PEP contracts for local and connected control."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Any, Callable, Dict, Iterable, Literal, Optional

import jwt
import yaml

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

    def __post_init__(self) -> None:
        _text(self.action, "action", required=True)
        _text(self.correlation_id, "correlation_id", required=True)
        if any(
            not isinstance(reference, str) or not reference.strip()
            for reference in self.evidence_references
        ):
            raise ValueError("evidence_references must contain non-empty strings")

    def to_remote_request(self) -> Dict[str, Any]:
        """Serialize only the stable public inputs to a remote control service."""
        return {
            "schema_version": DIRECTIVE_VERSION,
            "action": self.action,
            "integration_type": self.capability.integration_type,
            "technical_capabilities": list(self.capability.action_kinds),
            "correlation_id": self.correlation_id,
            "facts": dict(self.facts),
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


def _emit_decision(
    decision: DecisionEnvelope,
    observer: Optional[DecisionObserver],
) -> DecisionEnvelope:
    """Emit non-sensitive observability metadata without exposing action facts."""
    if decision.source == "local":
        _LOGGER.info(
            "pep_local_decision source=%s mode=%s decision=%s action=%s "
            "correlation_id=%s coverage=%s reason_code=%s would_enforce=%s",
            decision.source,
            decision.mode,
            decision.decision,
            decision.action,
            decision.correlation_id,
            decision.coverage,
            decision.reason_code,
            decision.would_enforce,
        )
    elif decision.mode == "observe":
        _LOGGER.info(
            "pep_observe_decision source=%s mode=%s decision=%s action=%s "
            "correlation_id=%s coverage=%s reason_code=%s would_enforce=%s",
            decision.source,
            decision.mode,
            decision.decision,
            decision.action,
            decision.correlation_id,
            decision.coverage,
            decision.reason_code,
            decision.would_enforce,
        )
    if observer is not None:
        try:
            observer(decision)
        except Exception:  # pragma: no cover - defensive observability boundary
            _LOGGER.warning("pep_decision_observer_failed", exc_info=True)
    return decision


def load_local_guardrail_manifest(path: str) -> LocalGuardrailManifest:
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("guardrail manifest must be a mapping")
    if set(raw) - {"schema_version", "mode", "actions"}:
        raise ValueError("guardrail manifest contains unsupported keys")
    if raw.get("schema_version") != MANIFEST_VERSION:
        raise ValueError("unsupported guardrail manifest schema_version")
    actions_raw = raw.get("actions")
    if not isinstance(actions_raw, dict) or not actions_raw:
        raise ValueError("actions must be a non-empty mapping")
    actions: Dict[str, _ActionRule] = {}
    for action, rule_raw in actions_raw.items():
        name = _text(action, "actions key", required=True)
        assert name is not None
        if not isinstance(rule_raw, dict) or set(rule_raw) - _RULE_KEYS:
            raise ValueError(f"actions.{name} contains unsupported keys")
        actions[name] = _ActionRule(
            readable_roots=_strings(
                rule_raw.get("readable_roots"), f"actions.{name}.readable_roots"
            ),
            writable_roots=_strings(
                rule_raw.get("writable_roots"), f"actions.{name}.writable_roots"
            ),
            max_bytes=_positive_int(
                rule_raw.get("max_bytes"), f"actions.{name}.max_bytes"
            ),
            max_results=_positive_int(
                rule_raw.get("max_results"), f"actions.{name}.max_results"
            ),
            max_patch_hunks=_positive_int(
                rule_raw.get("max_patch_hunks"), f"actions.{name}.max_patch_hunks"
            ),
            allowed_profiles=_strings(
                rule_raw.get("allowed_profiles"), f"actions.{name}.allowed_profiles"
            ),
            allowed_git_targets=_strings(
                rule_raw.get("allowed_git_targets"),
                f"actions.{name}.allowed_git_targets",
            ),
        )
    return LocalGuardrailManifest(mode=_mode(raw.get("mode")), actions=actions)


def _local_violation(
    intent: ActionIntent, manifest: LocalGuardrailManifest
) -> Optional[str]:
    rule = manifest.actions.get(intent.action)
    if not rule:
        return "action_not_configured"
    facts = intent.facts
    path = _text(facts.get("path"), "facts.path")
    access = _text(facts.get("access"), "facts.access")
    if rule.readable_roots or rule.writable_roots:
        if not path or access not in {"read", "write"}:
            return "path_access_required"
        if access == "read" and (
            not rule.readable_roots or not _under_roots(path, rule.readable_roots)
        ):
            return "path_not_readable"
        if access == "write" and (
            not rule.writable_roots or not _under_roots(path, rule.writable_roots)
        ):
            return "path_not_writable"
    numeric_checks = (
        ("bytes", rule.max_bytes, "bytes_limit_exceeded"),
        ("results", rule.max_results, "results_limit_exceeded"),
        ("patch_hunks", rule.max_patch_hunks, "patch_hunks_limit_exceeded"),
    )
    for fact, maximum, code in numeric_checks:
        if maximum is None:
            continue
        raw_value = facts.get(fact)
        if raw_value is None:
            return f"{fact}_required"
        if isinstance(raw_value, bool):
            return f"{fact}_invalid"
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return f"{fact}_invalid"
        if value < 0:
            return f"{fact}_invalid"
        if value > maximum:
            return code
    profile = _text(facts.get("profile"), "facts.profile")
    if rule.allowed_profiles:
        if not profile:
            return "profile_required"
        if profile not in rule.allowed_profiles:
            return "profile_not_allowed"
    target = _text(facts.get("git_target"), "facts.git_target")
    if rule.allowed_git_targets:
        if not target:
            return "git_target_required"
        if target not in rule.allowed_git_targets:
            return "git_target_not_allowed"
    return None


def _local_decision(
    intent: ActionIntent, manifest: LocalGuardrailManifest
) -> DecisionEnvelope:
    if not intent.capability.supports_local_guardrails:
        return DecisionEnvelope(
            "local",
            manifest.mode,
            "deny",
            False,
            intent.action,
            intent.correlation_id,
            "unsupported",
            "local_guardrails_unsupported",
            evidence_references=intent.evidence_references,
        )
    violation = _local_violation(intent, manifest)
    if not violation:
        return DecisionEnvelope(
            "local",
            manifest.mode,
            "allow",
            False,
            intent.action,
            intent.correlation_id,
            "covered",
            evidence_references=intent.evidence_references,
        )
    if manifest.mode == "observe":
        return DecisionEnvelope(
            "local",
            manifest.mode,
            "allow",
            True,
            intent.action,
            intent.correlation_id,
            "covered",
            violation,
            evidence_references=intent.evidence_references,
        )
    return DecisionEnvelope(
        "local",
        manifest.mode,
        "deny",
        True,
        intent.action,
        intent.correlation_id,
        "covered",
        violation,
        evidence_references=intent.evidence_references,
    )


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
    """Bind already signature-validated claims to one local action intent.

    This is used by the gateway/JWKS verifier after cryptographic validation.
    It deliberately performs the action and portable-scope checks separately,
    so another signed gateway capability cannot be substituted for this action.
    """
    if not isinstance(claims, dict):
        raise DirectiveValidationError("remote_directive_invalid")
    expected_issuer = _text(issuer, "directive issuer", required=True)
    if claims.get("iss") != expected_issuer:
        raise DirectiveValidationError("remote_directive_invalid")
    audiences = claims.get("aud")
    if isinstance(audiences, str):
        audiences = [audiences]
    if not isinstance(audiences, list) or audience not in audiences:
        raise DirectiveValidationError("remote_directive_invalid")
    if (
        claims.get("schema_version") != DIRECTIVE_VERSION
        or claims.get("typ") != "atellagent_control_directive"
    ):
        raise DirectiveValidationError("remote_directive_unsupported")
    directive_id = _text(claims.get("jti"), "directive jti", required=True)
    assert directive_id is not None
    try:
        expires_at = int(claims["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DirectiveValidationError("remote_directive_invalid") from exc
    if expires_at <= int(now()):
        raise DirectiveValidationError("remote_directive_stale")
    if not isinstance(claims.get("allowed"), bool):
        raise DirectiveValidationError("remote_directive_invalid")
    revoked = is_revoked or (lambda _directive_id: False)
    seen_directives = seen if seen is not None else set()
    if revoked(directive_id):
        raise DirectiveValidationError("remote_directive_revoked")
    if directive_id in seen_directives:
        raise DirectiveValidationError("remote_directive_replayed")
    if (
        _text(claims.get("action"), "directive action", required=True)
        != intent.action
        or claims.get("integration_type") != intent.capability.integration_type
        or _text(claims.get("correlation_id"), "directive correlation_id", required=True)
        != intent.correlation_id
    ):
        raise DirectiveValidationError("remote_directive_scope_mismatch")
    for attr in ("tenant_id", "execution_id", "workspace_id"):
        value = claims.get(attr)
        if value is not None and value != getattr(intent, attr):
            raise DirectiveValidationError("remote_directive_scope_mismatch")
    seen_directives.add(directive_id)
    return RemoteControlDirective(
        mode=_mode(claims.get("mode")),
        allowed=bool(claims.get("allowed")),
        action=intent.action,
        integration_type=intent.capability.integration_type,
        audience=audience,
        expires_at=expires_at,
        directive_id=directive_id,
        tenant_id=intent.tenant_id,
        execution_id=intent.execution_id,
        workspace_id=intent.workspace_id,
        evidence_requirements=_strings(
            claims.get("evidence_requirements"), "evidence_requirements"
        ),
        reason_code=_text(claims.get("reason_code"), "reason_code"),
    )


def evaluate_action(
    intent: ActionIntent,
    *,
    local_manifest: Optional[LocalGuardrailManifest] = None,
    remote_directive: Optional[RemoteControlDirective] = None,
    remote_required: bool = False,
    decision_observer: Optional[DecisionObserver] = None,
) -> DecisionEnvelope:
    """Combine bounded local technical restrictions with remote authority."""
    local = _local_decision(intent, local_manifest) if local_manifest else None
    if remote_directive is None:
        if remote_required:
            return _emit_decision(
                DecisionEnvelope(
                    "remote",
                    "enforce",
                    "deny",
                    True,
                    intent.action,
                    intent.correlation_id,
                    "unavailable",
                    "remote_directive_unavailable",
                    evidence_references=intent.evidence_references,
                ),
                decision_observer,
            )
        if local:
            return _emit_decision(local, decision_observer)
        return _emit_decision(
            DecisionEnvelope(
                "local",
                "enforce",
                "deny",
                True,
                intent.action,
                intent.correlation_id,
                "unsupported",
                "no_control_configuration",
                evidence_references=intent.evidence_references,
            ),
            decision_observer,
        )
    if (
        remote_directive.action != intent.action
        or remote_directive.integration_type != intent.capability.integration_type
    ):
        return _emit_decision(
            DecisionEnvelope(
                "remote",
                "enforce",
                "deny",
                True,
                intent.action,
                intent.correlation_id,
                "unavailable",
                "remote_directive_scope_mismatch",
                evidence_references=intent.evidence_references,
            ),
            decision_observer,
        )
    if local and local.decision == "deny":
        return _emit_decision(local, decision_observer)
    decision: Literal["allow", "deny"] = "allow" if remote_directive.allowed else "deny"
    would_enforce = decision == "deny"
    if remote_directive.mode == "observe" and decision == "deny":
        decision, would_enforce = "allow", True
    return _emit_decision(
        DecisionEnvelope(
            "remote",
            remote_directive.mode,
            decision,
            would_enforce,
            intent.action,
            intent.correlation_id,
            "covered",
            remote_directive.reason_code,
            remote_directive.evidence_requirements,
            intent.evidence_references,
        ),
        decision_observer,
    )


def evaluate_connected_action(
    intent: ActionIntent,
    *,
    verifier: RemoteDirectiveVerifier,
    encoded_directive: str,
    local_manifest: Optional[LocalGuardrailManifest] = None,
    decision_observer: Optional[DecisionObserver] = None,
) -> DecisionEnvelope:
    """Verify and apply a required remote directive with fail-closed errors."""
    try:
        directive = verifier.verify(encoded_directive, intent)
    except DirectiveValidationError as exc:
        return _emit_decision(
            DecisionEnvelope(
                "remote",
                "enforce",
                "deny",
                True,
                intent.action,
                intent.correlation_id,
                "unavailable",
                str(exc),
                evidence_references=intent.evidence_references,
            ),
            decision_observer,
        )
    return evaluate_action(
        intent,
        local_manifest=local_manifest,
        remote_directive=directive,
        remote_required=True,
        decision_observer=decision_observer,
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
    """Evaluate one explicit control source without authority fallback.

    ``cluster_directive`` is the default and requires a signed directive for the
    supplied action. ``local_manifest`` is available only to an integration that
    declares a local technical-interception capability. Selecting either source
    never silently selects the other one.
    """
    selected = str(source or "").strip().lower()
    if selected not in _CONTROL_SOURCES:
        raise ValueError("source must be 'cluster_directive' or 'local_manifest'")
    if selected == "local_manifest":
        return evaluate_action(
            intent,
            local_manifest=local_manifest,
            decision_observer=decision_observer,
        )
    if verifier is None or not str(encoded_directive or "").strip():
        return _emit_decision(
            DecisionEnvelope(
                "remote",
                "enforce",
                "deny",
                True,
                intent.action,
                intent.correlation_id,
                "unavailable",
                "remote_directive_unavailable",
                evidence_references=intent.evidence_references,
            ),
            decision_observer,
        )
    return evaluate_connected_action(
        intent,
        verifier=verifier,
        encoded_directive=str(encoded_directive),
        local_manifest=local_manifest,
        decision_observer=decision_observer,
    )
