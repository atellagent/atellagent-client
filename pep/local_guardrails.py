# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Parsing and evaluating optional local technical guardrails."""

from __future__ import annotations

from typing import Dict, Optional

import yaml

from .contracts import (
    ActionIntent, DecisionEnvelope, LocalGuardrailManifest, _ActionRule,
    _RULE_KEYS, MANIFEST_VERSION, _mode, _positive_int, _strings, _text, _under_roots,
)


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


def local_violation(
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


def local_decision(
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
    violation = local_violation(intent, manifest)
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
