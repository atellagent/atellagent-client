# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Versioned public agent participation request and tool contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
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

    @property
    def is_executable(self) -> bool:
        return bool(
            self.action_key
            and self.allowed
            and self.outcome in {None, "allow"}
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
    "ToolCallRequest",
    "ToolCallResult",
]
