# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Thin public control-plane contracts for callable-agent boundary governance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from atellagent_client.protocol.context import (
    normalize_portable_workflow_context,
    serialize_portable_workflow_context,
)

from atellagent_client.protocol.agent_contracts import (
    ExternalIdentityEvidence,
    GovernanceCallContext,
    GovernanceReceipt,
)


def coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def normalize_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def _normalize_public_workflow_context(
    workflow_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    normalized = normalize_portable_workflow_context(workflow_context)
    return serialize_portable_workflow_context(normalized) or {}


def extract_contract_workflow_context(payload: Any) -> Dict[str, Any]:
    payload_dict = payload if isinstance(payload, dict) else {}
    return normalize_portable_workflow_context(payload_dict.get("workflow_context")) or {}


def _normalize_external_subject(
    workflow_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    normalized = normalize_portable_workflow_context(workflow_context) or {}
    identity_context = (
        normalized.get("identity_context")
        if isinstance(normalized.get("identity_context"), dict)
        else {}
    )
    external_subject_identity = (
        identity_context.get("external_subject_identity")
        if isinstance(identity_context.get("external_subject_identity"), dict)
        else {}
    )
    return {
        "identity_provider": normalize_optional_text(
            external_subject_identity.get("identity_provider")
        ),
        "external_principal_id": normalize_optional_text(
            external_subject_identity.get("external_principal_id")
        ),
        "display_name": normalize_optional_text(
            external_subject_identity.get("display_name")
        ),
        "external_metadata": coerce_dict(external_subject_identity.get("metadata")),
    }


@dataclass(frozen=True)
class BoundaryIdentityEnvelope:
    bearer_token: Optional[str] = None
    identity_provider: Optional[str] = None
    external_principal_id: Optional[str] = None
    display_name: Optional[str] = None
    external_metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_identity_evidence(
        cls,
        identity: ExternalIdentityEvidence,
    ) -> "BoundaryIdentityEnvelope":
        return cls(
            bearer_token=normalize_optional_text(identity.bearer_token),
            identity_provider=normalize_optional_text(identity.identity_provider),
            external_principal_id=normalize_optional_text(
                identity.external_principal_id
            ),
            display_name=normalize_optional_text(identity.display_name),
            external_metadata=coerce_dict(identity.metadata),
        )

    @classmethod
    def from_workflow_context(
        cls,
        *,
        workflow_context: Optional[Dict[str, Any]],
        bearer_token: Optional[str] = None,
    ) -> "BoundaryIdentityEnvelope":
        subject = _normalize_external_subject(workflow_context)
        return cls(
            bearer_token=normalize_optional_text(bearer_token),
            identity_provider=subject["identity_provider"],
            external_principal_id=subject["external_principal_id"],
            display_name=subject["display_name"],
            external_metadata=subject["external_metadata"],
        )

    def to_gateway_payload(self) -> Dict[str, Any]:
        return {
            "external_bearer_token": normalize_optional_text(self.bearer_token),
            "identity_provider": normalize_optional_text(self.identity_provider),
            "external_principal_id": normalize_optional_text(
                self.external_principal_id
            ),
            "display_name": normalize_optional_text(self.display_name),
            "external_metadata": coerce_dict(self.external_metadata),
        }

    def to_bootstrap_payload(self) -> Dict[str, Any]:
        return self.to_gateway_payload()


@dataclass(frozen=True)
class BoundaryBootstrapRequest:
    identity: BoundaryIdentityEnvelope = field(default_factory=BoundaryIdentityEnvelope)

    @classmethod
    def from_identity_evidence(
        cls,
        identity: ExternalIdentityEvidence,
    ) -> "BoundaryBootstrapRequest":
        return cls(identity=BoundaryIdentityEnvelope.from_identity_evidence(identity))

    def to_payload(self) -> Dict[str, Any]:
        return self.identity.to_bootstrap_payload()


@dataclass(frozen=True)
class BoundaryBootstrapResponse:
    success: bool = False
    workflow_context: Dict[str, Any] = field(default_factory=dict)
    principal_created: bool = False
    binding_created: bool = False
    principal_expires_at_epoch: Optional[int] = None

    @classmethod
    def from_payload(cls, payload: Any) -> "BoundaryBootstrapResponse":
        payload_dict = payload if isinstance(payload, dict) else {}
        bootstrap = coerce_dict(payload_dict.get("bootstrap"))
        raw_exp = bootstrap.get("principal_expires_at_epoch")
        try:
            principal_expires_at_epoch = (
                int(raw_exp) if raw_exp is not None else None
            )
        except (TypeError, ValueError):
            principal_expires_at_epoch = None
        return cls(
            success=bool(payload_dict.get("success")),
            workflow_context=extract_contract_workflow_context(payload_dict),
            principal_created=bool(bootstrap.get("principal_created")),
            binding_created=bool(bootstrap.get("binding_created")),
            principal_expires_at_epoch=principal_expires_at_epoch,
        )

    @property
    def principal_context(self) -> Dict[str, Any]:
        return dict(self.workflow_context)


@dataclass(frozen=True)
class BoundaryPreflightRequest:
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    task_type: str = "mcp_tool"
    runtime_mode: str = "sdk"
    capabilities: list[str] = field(default_factory=list)
    action_key: Optional[str] = None
    tool_call_id: Optional[str] = None
    request_payload: Dict[str, Any] = field(default_factory=dict)
    workflow_context: Dict[str, Any] = field(default_factory=dict)
    policy_metadata: Dict[str, Any] = field(default_factory=dict)
    intent: Dict[str, Any] = field(default_factory=dict)
    resource: Dict[str, Any] = field(default_factory=dict)
    identity: BoundaryIdentityEnvelope = field(default_factory=BoundaryIdentityEnvelope)

    @classmethod
    def from_context(
        cls,
        *,
        context: GovernanceCallContext,
        workflow_context: Dict[str, Any],
        identity_forward: Optional[ExternalIdentityEvidence] = None,
    ) -> "BoundaryPreflightRequest":
        identity = (
            BoundaryIdentityEnvelope.from_identity_evidence(identity_forward)
            if identity_forward is not None
            else BoundaryIdentityEnvelope.from_identity_evidence(
                context.identity or ExternalIdentityEvidence()
            )
        )
        return cls(
            tool_name=context.tool_name,
            arguments=coerce_dict(context.arguments),
            task_type=context.task_type,
            runtime_mode=context.runtime_mode,
            capabilities=list(context.capabilities or []),
            action_key=normalize_optional_text(context.action_key),
            tool_call_id=normalize_optional_text(context.tool_call_id),
            request_payload=coerce_dict(context.request_payload),
            workflow_context=_normalize_public_workflow_context(workflow_context),
            policy_metadata=coerce_dict(context.policy_metadata),
            intent=coerce_dict(context.intent),
            resource=coerce_dict(context.resource),
            identity=identity,
        )

    def adapter_payload(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": coerce_dict(self.arguments),
            "task_type": self.task_type,
            "action_key": normalize_optional_text(self.action_key),
            "tool_call_id": normalize_optional_text(self.tool_call_id),
            "request_payload": coerce_dict(self.request_payload),
            "policy_metadata": coerce_dict(self.policy_metadata),
            "intent": coerce_dict(self.intent) or None,
            "resource": coerce_dict(self.resource) or None,
            "identity": self.identity.to_gateway_payload(),
        }

    def to_payload(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": coerce_dict(self.arguments),
            "task_type": self.task_type,
            "runtime_mode": self.runtime_mode,
            "capabilities": list(self.capabilities or []),
            "action_key": normalize_optional_text(self.action_key),
            "tool_call_id": normalize_optional_text(self.tool_call_id),
            "request_payload": coerce_dict(self.request_payload),
            "workflow_context": coerce_dict(self.workflow_context),
            "adapter_payload": self.adapter_payload(),
            "policy_metadata": coerce_dict(self.policy_metadata),
            "intent": coerce_dict(self.intent),
            "resource": coerce_dict(self.resource) or None,
            **self.identity.to_gateway_payload(),
        }


@dataclass(frozen=True)
class BoundaryPostflightRequest:
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    task_type: str = "mcp_tool"
    runtime_mode: str = "sdk"
    capabilities: list[str] = field(default_factory=list)
    action_key: str = ""
    tool_call_id: Optional[str] = None
    request_payload: Dict[str, Any] = field(default_factory=dict)
    workflow_context: Dict[str, Any] = field(default_factory=dict)
    policy_metadata: Dict[str, Any] = field(default_factory=dict)
    intent: Dict[str, Any] = field(default_factory=dict)
    resource: Dict[str, Any] = field(default_factory=dict)
    result_payload: Any = None
    success: bool = False
    error_message: Optional[str] = None
    error_type: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    identity: BoundaryIdentityEnvelope = field(default_factory=BoundaryIdentityEnvelope)

    @classmethod
    def from_result(
        cls,
        *,
        context: GovernanceCallContext,
        receipt: GovernanceReceipt,
        result_payload: Any,
        success: bool,
        error_message: Optional[str] = None,
        error_type: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
        resource: Optional[Dict[str, Any]] = None,
    ) -> "BoundaryPostflightRequest":
        return cls(
            tool_name=context.tool_name,
            arguments=coerce_dict(context.arguments),
            task_type=context.task_type,
            runtime_mode=context.runtime_mode,
            capabilities=list(context.capabilities or []),
            action_key=str(receipt.action_key or ""),
            tool_call_id=normalize_optional_text(context.tool_call_id),
            request_payload=coerce_dict(context.request_payload),
            workflow_context=_normalize_public_workflow_context(
                receipt.workflow_context
            ),
            policy_metadata=coerce_dict(context.policy_metadata),
            intent=coerce_dict(context.intent),
            resource=coerce_dict(
                resource if resource is not None else context.resource
            ),
            result_payload=result_payload,
            success=bool(success),
            error_message=normalize_optional_text(error_message),
            error_type=normalize_optional_text(error_type),
            evidence={
                **coerce_dict(context.evidence),
                **coerce_dict(evidence),
            },
            identity=BoundaryIdentityEnvelope.from_identity_evidence(
                context.identity or ExternalIdentityEvidence()
            ),
        )

    def adapter_payload(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": coerce_dict(self.arguments),
            "task_type": self.task_type,
            "action_key": self.action_key,
            "tool_call_id": normalize_optional_text(self.tool_call_id),
            "request_payload": coerce_dict(self.request_payload),
            "policy_metadata": coerce_dict(self.policy_metadata),
            "intent": coerce_dict(self.intent) or None,
            "resource": coerce_dict(self.resource) or None,
            "result_payload": self.result_payload,
            "success": bool(self.success),
            "error_message": normalize_optional_text(self.error_message),
            "error_type": normalize_optional_text(self.error_type),
            "evidence": coerce_dict(self.evidence),
            "identity": self.identity.to_gateway_payload(),
        }

    def to_payload(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": coerce_dict(self.arguments),
            "task_type": self.task_type,
            "runtime_mode": self.runtime_mode,
            "capabilities": list(self.capabilities or []),
            "action_key": self.action_key,
            "tool_call_id": normalize_optional_text(self.tool_call_id),
            "request_payload": coerce_dict(self.request_payload),
            "workflow_context": coerce_dict(self.workflow_context),
            "adapter_payload": self.adapter_payload(),
            "policy_metadata": coerce_dict(self.policy_metadata),
            "intent": coerce_dict(self.intent),
            "resource": coerce_dict(self.resource) or None,
            "result_payload": self.result_payload,
            "success": bool(self.success),
            "error_message": normalize_optional_text(self.error_message),
            "error_type": normalize_optional_text(self.error_type),
            "evidence": coerce_dict(self.evidence),
        }


def build_bootstrap_payload(*, identity: ExternalIdentityEvidence) -> Dict[str, Any]:
    """Serialize the declared external identity envelope for bootstrap."""
    return BoundaryBootstrapRequest.from_identity_evidence(identity).to_payload()


def build_preflight_payload(
    *,
    context: GovernanceCallContext,
    workflow_context: Dict[str, Any],
    identity_forward: Optional[ExternalIdentityEvidence] = None,
) -> Dict[str, Any]:
    """Serialize one declared action for cluster-side authorization."""
    return BoundaryPreflightRequest.from_context(
        context=context,
        workflow_context=workflow_context,
        identity_forward=identity_forward,
    ).to_payload()


def build_postflight_payload(
    *,
    context: GovernanceCallContext,
    receipt: GovernanceReceipt,
    result_payload: Any,
    success: bool,
    error_message: Optional[str] = None,
    error_type: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    resource: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Serialize the narrow outcome of a previously authorized action."""
    return BoundaryPostflightRequest.from_result(
        context=context,
        receipt=receipt,
        result_payload=result_payload,
        success=success,
        error_message=error_message,
        error_type=error_type,
        evidence=evidence,
        resource=resource,
    ).to_payload()


def receipt_from_preflight_response(
    *,
    payload: Any,
    workflow_context: Dict[str, Any],
    principal_context: Dict[str, Any],
    merge_context: Any,
) -> GovernanceReceipt:
    """Reduce a gateway response to the customer-visible decision receipt."""
    response_payload = payload if isinstance(payload, dict) else {}
    response_context = extract_contract_workflow_context(response_payload)
    effective_context = merge_context(
        explicit_context=response_context or workflow_context,
        principal_context=principal_context,
    )
    decision = coerce_dict(response_payload.get("decision"))
    return GovernanceReceipt(
        action_key=str(response_payload.get("action_key") or ""),
        allowed=bool(decision.get("allowed", False)),
        workflow_context=effective_context,
        outcome=normalize_optional_text(decision.get("outcome")),
        reason=normalize_optional_text(decision.get("reason")),
    )


__all__ = [
    "BoundaryBootstrapRequest",
    "BoundaryBootstrapResponse",
    "BoundaryIdentityEnvelope",
    "BoundaryPostflightRequest",
    "BoundaryPreflightRequest",
    "build_bootstrap_payload",
    "build_postflight_payload",
    "build_preflight_payload",
    "extract_contract_workflow_context",
    "receipt_from_preflight_response",
]
