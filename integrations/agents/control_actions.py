# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Action helpers for the callable-agent control client."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from atellagent_client.protocol.api import build_versioned_route
from atellagent_client.protocol.agent_contracts import (
    ExternalIdentityEvidence,
    GovernanceCallContext,
    GovernanceReceipt,
    GuardrailDecision,
)
from atellagent_client.sdk.client import reset_workflow_context, set_workflow_context
from atellagent_client.sdk.errors import PolicyViolationError

from .contracts import (
    BoundaryBootstrapResponse,
    build_bootstrap_payload,
    build_postflight_payload,
    build_preflight_payload,
    receipt_from_preflight_response,
)
from .identity_mode import FEDERATED_AGENT_IDENTITY

_BOOTSTRAP_PATH = "/agents/bootstrap-principal"
_PREFLIGHT_PATH = "/agents/boundary/preflight"
_POSTFLIGHT_PATH = "/agents/boundary/postflight"


def _normalize_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _policy_gate_error(payload: Dict[str, Any]) -> PolicyViolationError:
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    outcome = _normalize_optional_text(decision.get("outcome"))
    message = _normalize_optional_text(decision.get("reason")) or "Action blocked by remote control service"
    detail: Dict[str, Any] = {}
    if payload.get("action_key"):
        detail["action_key"] = payload.get("action_key")
    return PolicyViolationError(
        message,
        outcome or "remote_control_denied",
        detail,
    )


def bootstrap_sync(
    governance: Any,
    identity: ExternalIdentityEvidence,
) -> BoundaryBootstrapResponse:
    client, headers = governance._sync_headers({})
    response = client.post(
        f"{governance.gateway_session.base_url}{build_versioned_route(governance.config.api_version, _BOOTSTRAP_PATH)}",
        json=build_bootstrap_payload(identity=identity),
        headers=headers,
    )
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        governance._raise_gateway_error(response.status_code, payload)
    return BoundaryBootstrapResponse.from_payload(payload)


async def bootstrap_async(
    governance: Any,
    identity: ExternalIdentityEvidence,
) -> BoundaryBootstrapResponse:
    session, headers = await governance._async_headers({})
    response = await session.post(
        f"{governance.gateway_session.base_url}{build_versioned_route(governance.config.api_version, _BOOTSTRAP_PATH)}",
        json=build_bootstrap_payload(identity=identity),
        headers=headers,
    )
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        governance._raise_gateway_error(response.status_code, payload)
    return BoundaryBootstrapResponse.from_payload(payload)


def preflight_sync(governance: Any, context: GovernanceCallContext) -> GovernanceReceipt:
    principal_context: Dict[str, Any] = {}
    identity_forward: Optional[ExternalIdentityEvidence] = None
    if governance.identity_mode == FEDERATED_AGENT_IDENTITY:
        if not context.identity.bearer_token:
            raise PolicyViolationError(
                "Federated tool governance requires trusted identity evidence",
                "federated_identity_required",
            )
        bootstrap = governance.bootstrap_sync(context.identity)
        principal_context = bootstrap.principal_context
    elif context.identity.bearer_token or context.identity.identity_provider:
        identity_forward = context.identity
    workflow_context = governance._merge_context(
        explicit_context=context.workflow_context,
        principal_context=principal_context,
    )
    client, headers = governance._sync_headers(workflow_context)
    response = client.post(
        f"{governance.gateway_session.base_url}{build_versioned_route(governance.config.api_version, _PREFLIGHT_PATH)}",
        json=build_preflight_payload(
            context=context,
            workflow_context=workflow_context,
            identity_forward=identity_forward,
        ),
        headers=headers,
    )
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        governance._raise_gateway_error(response.status_code, payload)
    if isinstance(payload, dict) and payload.get("success") is False:
        raise _policy_gate_error(payload)
    return receipt_from_preflight_response(
        payload=payload,
        workflow_context=workflow_context,
        principal_context=principal_context,
        merge_context=governance._merge_context,
    )


async def preflight_async(governance: Any, context: GovernanceCallContext) -> GovernanceReceipt:
    principal_context: Dict[str, Any] = {}
    identity_forward: Optional[ExternalIdentityEvidence] = None
    if governance.identity_mode == FEDERATED_AGENT_IDENTITY:
        if not context.identity.bearer_token:
            raise PolicyViolationError(
                "Federated tool governance requires trusted identity evidence",
                "federated_identity_required",
            )
        bootstrap = await governance.bootstrap_async(context.identity)
        principal_context = bootstrap.principal_context
    elif context.identity.bearer_token or context.identity.identity_provider:
        identity_forward = context.identity
    workflow_context = governance._merge_context(
        explicit_context=context.workflow_context,
        principal_context=principal_context,
    )
    session, headers = await governance._async_headers(workflow_context)
    response = await session.post(
        f"{governance.gateway_session.base_url}{build_versioned_route(governance.config.api_version, _PREFLIGHT_PATH)}",
        json=build_preflight_payload(
            context=context,
            workflow_context=workflow_context,
            identity_forward=identity_forward,
        ),
        headers=headers,
    )
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        governance._raise_gateway_error(response.status_code, payload)
    if isinstance(payload, dict) and payload.get("success") is False:
        raise _policy_gate_error(payload)
    return receipt_from_preflight_response(
        payload=payload,
        workflow_context=workflow_context,
        principal_context=principal_context,
        merge_context=governance._merge_context,
    )


def postflight_sync(
    governance: Any,
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
    client, headers = governance._sync_headers(receipt.workflow_context)
    response = client.post(
        f"{governance.gateway_session.base_url}{build_versioned_route(governance.config.api_version, _POSTFLIGHT_PATH)}",
        json=build_postflight_payload(
            context=context,
            receipt=receipt,
            result_payload=result_payload,
            success=success,
            error_message=error_message,
            error_type=error_type,
            evidence=evidence,
            resource=resource,
        ),
        headers=headers,
    )
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        governance._raise_gateway_error(response.status_code, payload)


async def postflight_async(
    governance: Any,
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
    session, headers = await governance._async_headers(receipt.workflow_context)
    response = await session.post(
        f"{governance.gateway_session.base_url}{build_versioned_route(governance.config.api_version, _POSTFLIGHT_PATH)}",
        json=build_postflight_payload(
            context=context,
            receipt=receipt,
            result_payload=result_payload,
            success=success,
            error_message=error_message,
            error_type=error_type,
            evidence=evidence,
            resource=resource,
        ),
        headers=headers,
    )
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        governance._raise_gateway_error(response.status_code, payload)


def execute_sync(
    governance: Any,
    callback: Callable[[], Any],
    context: GovernanceCallContext,
    *,
    receipt: Optional[GovernanceReceipt] = None,
    evidence: Optional[Dict[str, Any]] = None,
    resource: Optional[Dict[str, Any]] = None,
) -> Any:
    effective_receipt = receipt or governance.preflight_sync(context)
    if not effective_receipt.is_executable:
        raise PolicyViolationError(
            effective_receipt.reason or "Action blocked before execution",
            effective_receipt.outcome or "policy_control_required",
            {
                "allowed": effective_receipt.allowed,
                "outcome": effective_receipt.outcome,
                "reason": effective_receipt.reason,
                "action_key": effective_receipt.action_key,
            },
        )
    governance.action_gate.enforce_sync(
        action=context.tool_name,
        integration_type=str(governance.config.integration_type),
        correlation_id=effective_receipt.action_key,
        encoded_directive=effective_receipt.control_directive,
        facts=context.arguments,
        workflow_context=effective_receipt.workflow_context,
    )
    token = set_workflow_context(effective_receipt.workflow_context)
    try:
        result = callback()
    except Exception as exc:
        reset_workflow_context(token)
        governance.postflight_sync(
            context,
            receipt=effective_receipt,
            result_payload=None,
            success=False,
            error_message=str(exc),
            error_type=exc.__class__.__name__,
            evidence=evidence,
            resource=resource,
        )
        raise
    reset_workflow_context(token)
    governance.postflight_sync(
        context,
        receipt=effective_receipt,
        result_payload=result,
        success=True,
        evidence=evidence,
        resource=resource,
    )
    return result


async def execute_async(
    governance: Any,
    callback: Callable[[], Any],
    context: GovernanceCallContext,
    *,
    receipt: Optional[GovernanceReceipt] = None,
    evidence: Optional[Dict[str, Any]] = None,
    resource: Optional[Dict[str, Any]] = None,
) -> Any:
    effective_receipt = receipt or await governance.preflight_async(context)
    if not effective_receipt.is_executable:
        raise PolicyViolationError(
            effective_receipt.reason or "Action blocked before execution",
            effective_receipt.outcome or "policy_control_required",
            {
                "allowed": effective_receipt.allowed,
                "outcome": effective_receipt.outcome,
                "reason": effective_receipt.reason,
                "action_key": effective_receipt.action_key,
            },
        )
    await governance.action_gate.enforce(
        action=context.tool_name,
        integration_type=str(governance.config.integration_type),
        correlation_id=effective_receipt.action_key,
        encoded_directive=effective_receipt.control_directive,
        facts=context.arguments,
        workflow_context=effective_receipt.workflow_context,
    )
    token = set_workflow_context(effective_receipt.workflow_context)
    try:
        result = callback()
        if hasattr(result, "__await__"):
            result = await result
    except Exception as exc:
        reset_workflow_context(token)
        await governance.postflight_async(
            context,
            receipt=effective_receipt,
            result_payload=None,
            success=False,
            error_message=str(exc),
            error_type=exc.__class__.__name__,
            evidence=evidence,
            resource=resource,
        )
        raise
    reset_workflow_context(token)
    await governance.postflight_async(
        context,
        receipt=effective_receipt,
        result_payload=result,
        success=True,
        evidence=evidence,
        resource=resource,
    )
    return result


def guardrail_sync(governance: Any, context: GovernanceCallContext) -> GuardrailDecision:
    try:
        receipt = governance.preflight_sync(context)
    except PolicyViolationError as exc:
        detail = exc.details if isinstance(exc.details, dict) else {}
        return GuardrailDecision(
            allowed=False,
            reason=str(exc),
            outcome=_normalize_optional_text(detail.get("outcome")) or "deny",
        )
    return GuardrailDecision(
        allowed=True,
        receipt=receipt,
        outcome=receipt.outcome,
    )


async def guardrail_async(governance: Any, context: GovernanceCallContext) -> GuardrailDecision:
    try:
        receipt = await governance.preflight_async(context)
    except PolicyViolationError as exc:
        detail = exc.details if isinstance(exc.details, dict) else {}
        return GuardrailDecision(
            allowed=False,
            reason=str(exc),
            outcome=_normalize_optional_text(detail.get("outcome")) or "deny",
        )
    return GuardrailDecision(
        allowed=True,
        receipt=receipt,
        outcome=receipt.outcome,
    )


__all__ = [
    "bootstrap_async",
    "bootstrap_sync",
    "execute_async",
    "execute_sync",
    "guardrail_async",
    "guardrail_sync",
    "postflight_async",
    "postflight_sync",
    "preflight_async",
    "preflight_sync",
]
