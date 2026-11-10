# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Action governance methods exposed by the public control client."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


from atellagent_client.protocol.agent_contracts import (
    ExternalIdentityEvidence,
    GovernanceCallContext,
    GovernanceReceipt,
    GuardrailDecision,
)

from . import control_actions as actions
from .contracts import BoundaryBootstrapResponse


class ControlActionMixin:
    def bootstrap_sync(
        self,
        identity: ExternalIdentityEvidence,
    ) -> BoundaryBootstrapResponse:
        return actions.bootstrap_sync(self, identity)

    async def bootstrap_async(
        self,
        identity: ExternalIdentityEvidence,
    ) -> BoundaryBootstrapResponse:
        return await actions.bootstrap_async(self, identity)

    def preflight_sync(self, context: GovernanceCallContext) -> GovernanceReceipt:
        return actions.preflight_sync(self, context)

    async def preflight_async(
        self, context: GovernanceCallContext
    ) -> GovernanceReceipt:
        return await actions.preflight_async(self, context)

    def postflight_sync(
        self,
        context: GovernanceCallContext,
        *,
        receipt: GovernanceReceipt,
        result_payload: Any,
        success: Optional[bool],
        error_message: Optional[str] = None,
        error_type: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
        resource: Optional[Dict[str, Any]] = None,
    ) -> None:
        return actions.postflight_sync(
            self,
            context,
            receipt=receipt,
            result_payload=result_payload,
            success=success,
            error_message=error_message,
            error_type=error_type,
            evidence=evidence,
            resource=resource,
        )

    async def postflight_async(
        self,
        context: GovernanceCallContext,
        *,
        receipt: GovernanceReceipt,
        result_payload: Any,
        success: Optional[bool],
        error_message: Optional[str] = None,
        error_type: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
        resource: Optional[Dict[str, Any]] = None,
    ) -> None:
        return await actions.postflight_async(
            self,
            context,
            receipt=receipt,
            result_payload=result_payload,
            success=success,
            error_message=error_message,
            error_type=error_type,
            evidence=evidence,
            resource=resource,
        )

    async def native_hook_outcome_async(
        self,
        *,
        action_key: str,
        action_binding_fingerprint: str,
        outcome_observation: str,
        success: Optional[bool],
        result_byte_length: Optional[int] = None,
        result_sha256: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> None:
        return await actions.native_hook_outcome_async(
            self,
            action_key=action_key,
            action_binding_fingerprint=action_binding_fingerprint,
            outcome_observation=outcome_observation,
            success=success,
            result_byte_length=result_byte_length,
            result_sha256=result_sha256,
            error_type=error_type,
        )

    def execute_sync(
        self,
        callback: Callable[[], Any],
        context: GovernanceCallContext,
        *,
        receipt: Optional[GovernanceReceipt] = None,
        evidence: Optional[Dict[str, Any]] = None,
        resource: Optional[Dict[str, Any]] = None,
    ) -> Any:
        return actions.execute_sync(
            self,
            callback,
            context,
            receipt=receipt,
            evidence=evidence,
            resource=resource,
        )

    async def execute_async(
        self,
        callback: Callable[[], Any],
        context: GovernanceCallContext,
        *,
        receipt: Optional[GovernanceReceipt] = None,
        evidence: Optional[Dict[str, Any]] = None,
        resource: Optional[Dict[str, Any]] = None,
    ) -> Any:
        return await actions.execute_async(
            self,
            callback,
            context,
            receipt=receipt,
            evidence=evidence,
            resource=resource,
        )

    def guardrail_sync(self, context: GovernanceCallContext) -> GuardrailDecision:
        return actions.guardrail_sync(self, context)

    async def guardrail_async(
        self, context: GovernanceCallContext
    ) -> GuardrailDecision:
        return await actions.guardrail_async(self, context)
