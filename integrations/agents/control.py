# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Shared gateway-backed control client for callable-agent provider integrations."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import httpx

from atellagent_client.sdk.config import (
    ServiceAccountConfig,
    load_service_account_config_from_yaml,
)
from atellagent_client.sdk.gateway.session import GatewaySession
from atellagent_client.protocol.agent_identity import has_bound_principal_identity
from atellagent_client.protocol.agent_contracts import (
    ExternalIdentityEvidence,
    GovernanceCallContext,
    GovernanceReceipt,
    GuardrailDecision,
)
from atellagent_client.protocol.context import (
    apply_workflow_headers,
    get_workflow_context,
    merge_portable_workflow_context,
    normalize_portable_workflow_context,
)
from atellagent_client.sdk.client_modules.runtime_authority import (
    apply_runtime_authority_headers,
)
from atellagent_client.sdk.errors import AuthenticationError, PolicyViolationError
from atellagent_client.sdk.operations_modules.common import extract_policy_detail

from . import control_actions as actions
from . import control_model_invocation as chat
from .contracts import BoundaryBootstrapResponse, extract_contract_workflow_context
from .identity_mode import (
    ExternalAgentIdentityMode,
    normalize_identity_mode,
    resolve_identity_mode_from_config,
)


class ExternalAgentGovernance:
    def __init__(
        self,
        config: ServiceAccountConfig,
        *,
        identity_mode: Optional[ExternalAgentIdentityMode] = None,
    ) -> None:
        self.config = config
        self.gateway_session = GatewaySession.from_service_account_config(config)
        self.identity_mode = normalize_identity_mode(
            identity_mode or resolve_identity_mode_from_config(config)
        )

    @classmethod
    def from_config_path(
        cls,
        config_path: str,
        *,
        identity_mode: Optional[ExternalAgentIdentityMode] = None,
    ) -> "ExternalAgentGovernance":
        config = load_service_account_config_from_yaml(config_path)
        return cls(config, identity_mode=identity_mode)

    def _merge_context(
        self,
        *,
        explicit_context: Optional[Dict[str, Any]],
        principal_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        merged = merge_portable_workflow_context(
            explicit_context,
            get_workflow_context(),
        )
        merged = merge_portable_workflow_context(principal_context, merged)
        return normalize_portable_workflow_context(merged) or {}

    def _raise_gateway_error(self, status_code: int, payload: Any) -> None:
        detail = extract_policy_detail(payload)
        if status_code == 403:
            message = (
                detail.get("message")
                or detail.get("error")
                or detail.get("detail")
                or "Action blocked by policy"
            )
            raise PolicyViolationError(
                str(message).strip() or "Action blocked by policy",
                str(detail.get("violation_type") or "policy_violation"),
                detail,
            )
        if isinstance(payload, dict):
            message = payload.get("detail") or payload.get("message") or payload.get("error")
            if isinstance(message, str) and message.strip():
                raise RuntimeError(message.strip())
        raise RuntimeError(f"gateway governance call failed with status {status_code}")

    def _extract_principal_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return extract_contract_workflow_context(payload)

    @staticmethod
    def _has_bound_principal_context(workflow_context: Optional[Dict[str, Any]]) -> bool:
        return has_bound_principal_identity(workflow_context)

    def _sync_headers(self, workflow_context: Dict[str, Any]) -> tuple[httpx.Client, Dict[str, str]]:
        client, headers = self.gateway_session.get_authenticated_request_context_sync()
        if client is None or headers is None:
            raise AuthenticationError("Failed to authenticate service account")
        return client, apply_runtime_authority_headers(
            apply_workflow_headers(headers, workflow_context=workflow_context)
        )

    async def _async_headers(
        self,
        workflow_context: Dict[str, Any],
    ) -> tuple[httpx.AsyncClient, Dict[str, str]]:
        session, headers = await self.gateway_session.get_authenticated_request_context()
        if session is None or headers is None:
            raise AuthenticationError("Failed to authenticate service account")
        return session, apply_runtime_authority_headers(
            apply_workflow_headers(headers, workflow_context=workflow_context)
        )

    def _resolve_model_workflow_context_sync(
        self,
        *,
        workflow_context: Optional[Dict[str, Any]],
        identity: Optional[ExternalIdentityEvidence],
    ) -> Dict[str, Any]:
        return chat.resolve_model_workflow_context_sync(
            self,
            workflow_context=workflow_context,
            identity=identity,
        )

    async def _resolve_model_workflow_context_async(
        self,
        *,
        workflow_context: Optional[Dict[str, Any]],
        identity: Optional[ExternalIdentityEvidence],
    ) -> Dict[str, Any]:
        return await chat.resolve_model_workflow_context_async(
            self,
            workflow_context=workflow_context,
            identity=identity,
        )

    @staticmethod
    def _build_model_invocation_payload(
        *,
        messages: List[Dict[str, Any]],
        memory_thread_id: str,
        stream: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return chat.build_model_invocation_payload(
            messages=messages,
            memory_thread_id=memory_thread_id,
            stream=stream,
            **kwargs,
        )

    def _invoke_model_sync(
        self,
        *,
        workflow_context: Dict[str, Any],
        payload: Dict[str, Any],
        poll_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 0.5,
    ) -> Dict[str, Any]:
        return chat.invoke_model_sync(
            self,
            workflow_context=workflow_context,
            payload=payload,
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

    async def _invoke_model_async(
        self,
        *,
        workflow_context: Dict[str, Any],
        payload: Dict[str, Any],
        poll_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 0.5,
    ) -> Dict[str, Any]:
        return await chat.invoke_model_async(
            self,
            workflow_context=workflow_context,
            payload=payload,
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

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

    async def preflight_async(self, context: GovernanceCallContext) -> GovernanceReceipt:
        return await actions.preflight_async(self, context)

    def postflight_sync(
        self,
        context: GovernanceCallContext,
        *,
        receipt: GovernanceReceipt,
        result_payload: Any,
        success: bool,
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
        success: bool,
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

    async def guardrail_async(self, context: GovernanceCallContext) -> GuardrailDecision:
        return await actions.guardrail_async(self, context)

    def governed_model_call_sync(
        self,
        *,
        messages: List[Dict[str, Any]],
        memory_thread_id: str,
        workflow_context: Optional[Dict[str, Any]] = None,
        identity: Optional[ExternalIdentityEvidence] = None,
        stream: bool = False,
        poll_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 0.5,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return chat.governed_model_call_sync(
            self,
            messages=messages,
            memory_thread_id=memory_thread_id,
            workflow_context=workflow_context,
            identity=identity,
            stream=stream,
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            **kwargs,
        )

    async def governed_model_call_async(
        self,
        *,
        messages: List[Dict[str, Any]],
        memory_thread_id: str,
        workflow_context: Optional[Dict[str, Any]] = None,
        identity: Optional[ExternalIdentityEvidence] = None,
        stream: bool = False,
        poll_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 0.5,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return await chat.governed_model_call_async(
            self,
            messages=messages,
            memory_thread_id=memory_thread_id,
            workflow_context=workflow_context,
            identity=identity,
            stream=stream,
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            **kwargs,
        )

    def close(self) -> None:
        self.gateway_session.close_sync()

    async def close_async(self) -> None:
        await self.gateway_session.close_async()


__all__ = [
    "ExternalAgentGovernance",
    "ExternalIdentityEvidence",
    "GovernanceCallContext",
    "GovernanceReceipt",
    "GuardrailDecision",
]
