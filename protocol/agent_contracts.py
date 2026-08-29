# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Versioned public agent participation request and tool contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Dict, List, Literal, Optional
import uuid


@dataclass
class ToolCallRequest:
    call_id: str
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    target_binding: Optional[str] = None
    timeout_seconds: Optional[float] = None


@dataclass
class ToolCallResult:
    call_id: str
    success: bool
    content: Optional[str] = None
    error: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None
    duration_ms: Optional[int] = None


@dataclass
class AgentTurnRequest:
    messages: List[Dict[str, Any]]
    workflow_context: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 8
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    fail_closed: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.messages, list) or not self.messages:
            raise ValueError("AgentTurnRequest.messages must be a non-empty list")
        if self.max_iterations < 1:
            raise ValueError("AgentTurnRequest.max_iterations must be >= 1")
        if not self.fail_closed:
            raise ValueError("AgentTurnRequest.fail_closed must be true")


@dataclass(frozen=True)
class ExternalIdentityEvidence:
    """Customer-supplied evidence for a cluster-authoritative agent binding."""

    bearer_token: Optional[str] = None
    identity_provider: Optional[str] = None
    external_principal_id: Optional[str] = None
    display_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GovernanceCallContext:
    """Declared facts that an agent adapter may submit for one action."""

    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    task_type: str = "mcp_tool"
    runtime_mode: str = "sdk"
    capabilities: List[str] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    action_key: Optional[str] = None
    request_payload: Dict[str, Any] = field(default_factory=dict)
    workflow_context: Dict[str, Any] = field(default_factory=dict)
    policy_metadata: Dict[str, Any] = field(default_factory=dict)
    intent: Dict[str, Any] = field(default_factory=dict)
    resource: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    identity: ExternalIdentityEvidence = field(default_factory=ExternalIdentityEvidence)


@dataclass(frozen=True)
class GovernanceReceipt:
    """Narrow cluster decision returned to a customer-operated adapter."""

    action_key: str
    allowed: bool
    workflow_context: Dict[str, Any]
    outcome: Optional[str] = None
    reason: Optional[str] = None
    decision_id: Optional[str] = None
    coverage: Optional[str] = None
    obligations: tuple[Dict[str, Any], ...] = ()
    control_directive: Optional[str] = None
    directive_expires_at: Optional[str] = None

    @property
    def is_executable(self) -> bool:
        return bool(
            self.action_key
            and self.allowed
            and self.outcome in {None, "allow"}
        )


@dataclass(frozen=True)
class ModelDecisionRequest:
    """Portable input to a synchronous model-admission decision endpoint."""

    input_scope: Literal["turn_entry", "full_model_request"]
    messages: List[Dict[str, Any]]
    model: Optional[str] = None
    provider: Optional[str] = None
    tool_definitions: Optional[List[Dict[str, Any]]] = None
    provider_request: Optional[Dict[str, Any]] = None
    generation: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("ModelDecisionRequest.messages must be non-empty")
        for message in self.messages:
            if not isinstance(message, dict):
                raise ValueError("ModelDecisionRequest.messages must contain objects")
            role = str(message.get("role") or "").strip()
            if role not in {"system", "user", "assistant", "tool", "function"}:
                raise ValueError("ModelDecisionRequest message role is unsupported")
            if not isinstance(message.get("content"), str):
                raise ValueError("ModelDecisionRequest message content must be text")
        if self.input_scope == "full_model_request" and (
            not str(self.model or "").strip() or not str(self.provider or "").strip()
        ):
            raise ValueError("full_model_request requires model and provider")
        if self.input_scope == "turn_entry" and (self.model or self.provider):
            raise ValueError("turn_entry must not provide model or provider")
        if self.input_scope == "turn_entry" and self.provider_request:
            raise ValueError("turn_entry must not provide provider_request")
        if self.input_scope == "turn_entry" and (
            len(self.messages) != 1 or self.messages[0].get("role") != "user"
        ):
            raise ValueError("turn_entry requires exactly one user message")
        if self.provider_request is not None and not isinstance(self.provider_request, dict):
            raise ValueError("provider_request must be an object when provided")

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "input_scope": self.input_scope,
            "messages": list(self.messages),
        }
        if self.model:
            payload["model"] = self.model
        if self.provider:
            payload["provider"] = self.provider
        if self.tool_definitions is not None:
            payload["tool_definitions"] = list(self.tool_definitions)
        if self.provider_request is not None:
            payload["provider_request"] = dict(self.provider_request)
        payload.update(dict(self.generation))
        return payload

    @property
    def request_fingerprint(self) -> str:
        """Opaque binding for the exact public decision payload."""
        return sha256(
            json.dumps(
                self.to_payload(), sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class ModelDecision:
    """Customer-safe decision projection; detector and policy internals stay remote."""

    outcome: Literal["allow", "deny"]
    enforcement: Literal["enforced", "advisory"]
    input_scope: Literal["turn_entry", "full_model_request"]
    evaluated: Dict[str, str]
    reason_code: str
    reason: str
    obligations: tuple[Dict[str, Any], ...]
    valid_until: Optional[str]
    decision_id: str
    correlation_id: str
    request_fingerprint: str

    @classmethod
    def from_payload(cls, payload: Any) -> "ModelDecision":
        values = payload if isinstance(payload, dict) else {}
        outcome = str(values.get("outcome") or "").strip().lower()
        enforcement = str(values.get("enforcement") or "").strip().lower()
        input_scope = str(values.get("input_scope") or "").strip()
        if outcome not in {"allow", "deny"} or enforcement not in {"enforced", "advisory"}:
            raise ValueError("model decision response is invalid")
        if input_scope not in {"turn_entry", "full_model_request"}:
            raise ValueError("model decision response has an unsupported input scope")
        obligations = values.get("obligations")
        request_fingerprint = str(values.get("request_fingerprint") or "").strip()
        if len(request_fingerprint) != 64:
            raise ValueError("model decision response is missing a request fingerprint")
        return cls(
            outcome=outcome,  # type: ignore[arg-type]
            enforcement=enforcement,  # type: ignore[arg-type]
            input_scope=input_scope,  # type: ignore[arg-type]
            evaluated=dict(values.get("evaluated") or {}),
            reason_code=str(values.get("reason_code") or "policy.unknown"),
            reason=str(values.get("reason") or "Policy decision unavailable"),
            obligations=tuple(
                dict(item) for item in (obligations or []) if isinstance(item, dict)
            ),
            valid_until=(str(values["valid_until"]) if values.get("valid_until") else None),
            decision_id=str(values.get("decision_id") or ""),
            correlation_id=str(values.get("correlation_id") or ""),
            request_fingerprint=request_fingerprint,
        )


@dataclass(frozen=True)
class GuardrailDecision:
    """Provider-friendly representation of a governed preflight result."""

    allowed: bool
    reason: Optional[str] = None
    receipt: Optional[GovernanceReceipt] = None
    outcome: Optional[str] = None


__all__ = [
    "AgentTurnRequest",
    "ExternalIdentityEvidence",
    "GovernanceCallContext",
    "GovernanceReceipt",
    "GuardrailDecision",
    "ModelDecision",
    "ModelDecisionRequest",
    "ToolCallRequest",
    "ToolCallResult",
]
