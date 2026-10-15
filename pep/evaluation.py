# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Compose local technical guardrails and signed remote authority."""

from __future__ import annotations

import logging
from typing import Literal, Optional

from .contracts import (
    ActionIntent, ControlSource, DecisionEnvelope, DecisionObserver,
    DirectiveValidationError, LocalGuardrailManifest, RemoteControlDirective,
    RemoteDirectiveVerifier, _local_decision,
)

_CONTROL_SOURCES = frozenset({"cluster_directive", "local_manifest"})
_LOGGER = logging.getLogger("atellagent_client.pep")


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
