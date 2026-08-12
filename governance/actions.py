# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Explicit managed and free-local gates for customer runtime actions."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from atellagent_client.pep import (
    ActionIntent,
    GatewayDirectiveVerifier,
    IntegrationCapability,
    LocalGuardrailManifest,
    evaluate_controlled_action,
    load_local_guardrail_manifest,
)
from atellagent_client.sdk.config import load_service_account_config_from_yaml


class ActionDenied(PermissionError):
    """The explicitly selected action-control source denied an action."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or "remote_action_denied")
        super().__init__(self.reason_code)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _context_value(context: Optional[Mapping[str, Any]], key: str) -> Optional[str]:
    if not isinstance(context, Mapping):
        return None
    return _text(context.get(key)) or None


@dataclass(frozen=True)
class RuntimeActionGate:
    """Evaluate exactly one configured control source without fallback."""

    source: str
    directive_verifier: Optional[Any] = None
    local_manifest: Optional[LocalGuardrailManifest] = None

    @classmethod
    def from_config_path(cls, config_path: str) -> "RuntimeActionGate":
        config = load_service_account_config_from_yaml(config_path)
        if config.control_source == "local_manifest":
            return cls.from_local_manifest(
                str(config.local_guardrail_manifest_path),
                expected_mode=config.local_guardrail_mode,
            )
        return cls(
            source="cluster_directive",
            directive_verifier=GatewayDirectiveVerifier(config),
        )

    @classmethod
    def from_local_manifest(
        cls,
        path: str,
        *,
        expected_mode: Optional[str] = None,
    ) -> "RuntimeActionGate":
        manifest = load_local_guardrail_manifest(path)
        normalized_expected = str(expected_mode or "").strip().lower() or None
        if normalized_expected and manifest.mode != normalized_expected:
            raise ValueError(
                "local guardrail manifest mode does not match the provisioned mode"
            )
        return cls(
            source="local_manifest",
            local_manifest=manifest,
        )

    async def enforce(
        self,
        *,
        action: str,
        integration_type: str,
        correlation_id: str,
        encoded_directive: Optional[str] = None,
        facts: Optional[Mapping[str, Any]] = None,
        workflow_context: Optional[Mapping[str, Any]] = None,
    ) -> None:
        normalized_action = _text(action)
        normalized_integration_type = _text(integration_type)
        normalized_correlation_id = _text(correlation_id)
        if not normalized_action or not normalized_integration_type or not normalized_correlation_id:
            raise ActionDenied("control_input_invalid")
        intent = ActionIntent(
            action=normalized_action,
            capability=IntegrationCapability(
                integration_type=normalized_integration_type,
                intercepts_actions=True,
                action_kinds=(normalized_action,),
            ),
            correlation_id=normalized_correlation_id,
            facts=dict(facts or {}),
            tenant_id=_context_value(workflow_context, "tenant_id"),
            execution_id=_context_value(workflow_context, "execution_id"),
            workspace_id=_context_value(workflow_context, "workspace_id"),
        )
        try:
            directive = None
            if self.source == "cluster_directive" and self.directive_verifier is not None:
                directive = self.directive_verifier.verify(str(encoded_directive or ""), intent)
                if inspect.isawaitable(directive):
                    directive = await directive
            decision = evaluate_controlled_action(
                intent,
                source=self.source,
                verifier=None,
                encoded_directive=None,
                local_manifest=self.local_manifest,
            ) if self.source == "local_manifest" else evaluate_controlled_action(
                intent,
                source="cluster_directive",
                verifier=_VerifiedDirectiveAdapter(directive),
                encoded_directive=str(encoded_directive or ""),
            )
        except Exception as exc:
            raise ActionDenied(str(exc) or "control_unavailable") from exc
        if decision.decision != "allow":
            raise ActionDenied(
                decision.reason_code or "remote_action_denied"
            )


class _VerifiedDirectiveAdapter:
    """Present an already verified directive to the source-selection contract."""

    def __init__(self, directive: Any) -> None:
        self._directive = directive

    def verify(self, _encoded: str, _intent: ActionIntent) -> Any:
        if self._directive is None:
            raise ValueError("remote_directive_unavailable")
        return self._directive


__all__ = ["ActionDenied", "RuntimeActionGate"]
