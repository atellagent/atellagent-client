# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Shared gateway-backed control client for callable-agent provider integrations."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping, Optional


from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.gateway.session import GatewaySession
from atellagent_client.protocol.agent_contracts import (
    ExternalIdentityEvidence,
    GovernanceCallContext,
    GovernanceReceipt,
    GuardrailDecision,
    ModelDecision,
    ModelDecisionRequest,
)
from atellagent_client.governance import RuntimeActionGate

from .identity_mode import (
    resolve_identity_mode_from_config,
)


from .control_gateway_transport import ControlGatewayTransportMixin
from .control_mcp import ControlMCPMixin
from .control_model_facade import ControlModelMixin
from .control_action_facade import ControlActionMixin


class ExternalAgentGovernance(
    ControlGatewayTransportMixin,
    ControlMCPMixin,
    ControlModelMixin,
    ControlActionMixin,
):
    """Public governance client composed from transport and operation owners."""

    def __init__(
        self,
        config: ServiceAccountConfig,
        *,
        session_provider: Optional[Callable[[], GatewaySession]] = None,
        local_user_headers_provider: Optional[
            Callable[[], Awaitable[Mapping[str, str]]]
        ] = None,
        device_proof_provider: Optional[
            Callable[[str, str, Any], Mapping[str, str]]
        ] = None,
    ) -> None:
        self.config = config
        self._session_provider = session_provider
        self._gateway_session = (
            None
            if session_provider is not None
            else GatewaySession.from_service_account_config(config)
        )
        self._owns_gateway_session = session_provider is None
        self._local_user_headers_provider = local_user_headers_provider
        self._device_proof_provider = device_proof_provider
        self.identity_mode = resolve_identity_mode_from_config(config)
        self.action_gate = RuntimeActionGate.from_config(config)

    @property
    def gateway_session(self) -> GatewaySession:
        """Return the current gateway session for this execution boundary."""
        if self._session_provider is not None:
            return self._session_provider()
        if self._gateway_session is None:  # pragma: no cover - constructor invariant
            raise RuntimeError("gateway session is unavailable")
        return self._gateway_session

    def close(self) -> None:
        if self._owns_gateway_session:
            self.gateway_session.close_sync()

    async def close_async(self) -> None:
        if self._owns_gateway_session:
            await self.gateway_session.close_async()


__all__ = [
    "ExternalAgentGovernance",
    "ExternalIdentityEvidence",
    "GovernanceCallContext",
    "GovernanceReceipt",
    "GuardrailDecision",
    "ModelDecision",
    "ModelDecisionRequest",
]
