# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Provider-neutral model-governance session primitives.

This module contains transport selection and local PEP sequencing only.  It
does not evaluate policy, select providers, issue directives, or interpret
detector output.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional

from atellagent_client.integrations.agents.control import ExternalAgentGovernance
from atellagent_client.protocol.agent_contracts import (
    ExternalIdentityEvidence,
    ModelDecision,
    ModelDecisionRequest,
)
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError


class ModelGovernanceMode(str, Enum):
    DECISION = "decision"
    ROUTE = "route"


@dataclass(frozen=True)
class GovernedSessionResult:
    """One canonical result shape for decision and route strategies."""

    payload: Dict[str, Any]
    mode: ModelGovernanceMode
    decisions: tuple[ModelDecision, ...] = ()
    provider_payload: Optional[Any] = None


@dataclass(frozen=True)
class DecisionModelTransport:
    """Decision-mode transport that leaves provider transport with the host."""

    governance: ExternalAgentGovernance
    workflow_context: Optional[Dict[str, Any]] = None
    identity: Optional[ExternalIdentityEvidence] = None

    async def invoke(
        self,
        *,
        decision_request: ModelDecisionRequest,
        invoke: Callable[[], Awaitable[Any] | Any],
        on_admitted: Callable[[ModelDecision], None],
    ) -> tuple[ModelDecision, Any]:
        """Obtain one decision before exactly one native provider request."""
        decision = await self.governance.model_decision_async(
            decision_request,
            workflow_context=self.workflow_context,
            identity=self.identity,
        )
        if decision.input_scope != decision_request.input_scope:
            raise PolicyTransportError("model decision response scope did not match the request")
        if decision.outcome != "allow":
            raise PolicyViolationError(
                decision.reason,
                decision.reason_code,
                {
                    "decision_id": decision.decision_id,
                    "correlation_id": decision.correlation_id,
                },
            )
        if decision.obligations:
            raise PolicyViolationError(
                "model decision requires an unsupported obligation",
                "policy.obligation_unsupported",
                {
                    "decision_id": decision.decision_id,
                    "correlation_id": decision.correlation_id,
                },
            )
        on_admitted(decision)
        value = invoke()
        return decision, await value if inspect.isawaitable(value) else value


@dataclass(frozen=True)
class RouteModelTransport:
    """Route-mode transport that never has a native-provider callback."""

    governance: ExternalAgentGovernance
    workflow_context: Optional[Dict[str, Any]] = None
    identity: Optional[ExternalIdentityEvidence] = None

    async def invoke(
        self,
        *,
        messages: list[Dict[str, Any]],
        memory_thread_id: str,
        **model_request: Any,
    ) -> Dict[str, Any]:
        """Run one routed model turn without a native-provider fallback."""
        payload = await self.governance.governed_model_call_async(
            messages=messages,
            memory_thread_id=memory_thread_id,
            workflow_context=self.workflow_context,
            identity=self.identity,
            **model_request,
        )
        return dict(payload)


@dataclass
class GovernedProviderSession:
    """Own one governed provider conversation without transport fallback."""

    governance: ExternalAgentGovernance
    mode: ModelGovernanceMode
    workflow_context: Optional[Dict[str, Any]] = None
    identity: Optional[ExternalIdentityEvidence] = None
    _decisions: list[ModelDecision] = field(default_factory=list, init=False)
    _transport: DecisionModelTransport | RouteModelTransport = field(init=False)

    def __post_init__(self) -> None:
        self.mode = ModelGovernanceMode(self.mode)
        transport_type = (
            DecisionModelTransport
            if self.mode is ModelGovernanceMode.DECISION
            else RouteModelTransport
        )
        self._transport = transport_type(
            governance=self.governance,
            workflow_context=self.workflow_context,
            identity=self.identity,
        )

    @property
    def transport(self) -> DecisionModelTransport | RouteModelTransport:
        """Expose the selected transport without exposing a fallback path."""
        return self._transport

    @property
    def decisions(self) -> tuple[ModelDecision, ...]:
        """Return safe decision correlation evidence accumulated by this session."""
        return tuple(self._decisions)

    async def native_turn(
        self,
        *,
        decision_request: ModelDecisionRequest,
        invoke: Callable[[], Awaitable[Any] | Any],
    ) -> GovernedSessionResult:
        """Check one native-provider turn and return the canonical session result."""
        if self.mode is not ModelGovernanceMode.DECISION:
            raise RuntimeError("native provider turns require decision governance mode")
        if not isinstance(self._transport, DecisionModelTransport):
            raise RuntimeError("decision transport was not configured")
        decision, value = await self._transport.invoke(
            decision_request=decision_request,
            invoke=invoke,
            on_admitted=self._decisions.append,
        )
        return GovernedSessionResult(
            payload={
                "status": "native_response",
                "decision_id": decision.decision_id,
                "correlation_id": decision.correlation_id,
            },
            mode=self.mode,
            decisions=self.decisions,
            provider_payload=value,
        )

    async def route_turn(
        self,
        *,
        messages: list[Dict[str, Any]],
        memory_thread_id: str,
        **model_request: Any,
    ) -> GovernedSessionResult:
        """Invoke the route transport; native provider transport is unreachable."""
        if self.mode is not ModelGovernanceMode.ROUTE:
            raise RuntimeError("route turns require route governance mode")
        if not isinstance(self._transport, RouteModelTransport):
            raise RuntimeError("route transport was not configured")
        payload = await self._transport.invoke(
            messages=messages,
            memory_thread_id=memory_thread_id,
            **model_request,
        )
        return GovernedSessionResult(
            payload=dict(payload),
            mode=self.mode,
            provider_payload=None,
        )


__all__ = [
    "GovernedProviderSession",
    "GovernedSessionResult",
    "DecisionModelTransport",
    "ModelGovernanceMode",
    "RouteModelTransport",
]
