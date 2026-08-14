# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Model-call helpers for the callable-agent control client."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from atellagent_client.protocol.api import build_versioned_route
from atellagent_client.protocol.agent_contracts import (
    ExternalIdentityEvidence,
    ModelDecision,
    ModelDecisionRequest,
)
from atellagent_client.sdk.client import reset_workflow_context, set_workflow_context
from atellagent_client.sdk.operations_modules.invocation_errors import (
    raise_forbidden_invocation_error,
)
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError

from .identity_mode import FEDERATED_AGENT_IDENTITY

_MODEL_INVOCATIONS_PATH = "/model-invocations"
_MODEL_DECISIONS_PATH = "/model-decisions"


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def resolve_model_workflow_context_sync(
    governance: Any,
    *,
    workflow_context: Optional[Dict[str, Any]],
    identity: Optional[ExternalIdentityEvidence],
) -> Dict[str, Any]:
    explicit_context = _coerce_dict(workflow_context)
    if governance._has_bound_principal_context(explicit_context):
        return governance._merge_context(explicit_context=explicit_context)

    identity_evidence = identity or ExternalIdentityEvidence()
    principal_context: Dict[str, Any] = {}
    has_identity_evidence = bool(
        identity_evidence.bearer_token
        or (
            identity_evidence.identity_provider and identity_evidence.external_principal_id
        )
    )
    if governance.identity_mode == FEDERATED_AGENT_IDENTITY and not has_identity_evidence:
        raise RuntimeError(
            "federated governed model calls require external agent identity "
            "evidence or workflow_context with a bound principal"
        )
    if governance.identity_mode == FEDERATED_AGENT_IDENTITY or has_identity_evidence:
        bootstrap = governance.bootstrap_sync(identity_evidence)
        principal_context = bootstrap.principal_context

    resolved = governance._merge_context(
        explicit_context=explicit_context,
        principal_context=principal_context,
    )
    if not governance._has_bound_principal_context(resolved):
        raise RuntimeError(
            "governed model calls require a resolved agent principal binding; "
            "provide federated identity or workflow_context with bound principal fields"
        )
    return resolved


async def resolve_model_workflow_context_async(
    governance: Any,
    *,
    workflow_context: Optional[Dict[str, Any]],
    identity: Optional[ExternalIdentityEvidence],
) -> Dict[str, Any]:
    explicit_context = _coerce_dict(workflow_context)
    if governance._has_bound_principal_context(explicit_context):
        return governance._merge_context(explicit_context=explicit_context)

    identity_evidence = identity or ExternalIdentityEvidence()
    principal_context: Dict[str, Any] = {}
    has_identity_evidence = bool(
        identity_evidence.bearer_token
        or (
            identity_evidence.identity_provider and identity_evidence.external_principal_id
        )
    )
    if governance.identity_mode == FEDERATED_AGENT_IDENTITY and not has_identity_evidence:
        raise RuntimeError(
            "federated governed model calls require external agent identity "
            "evidence or workflow_context with a bound principal"
        )
    if governance.identity_mode == FEDERATED_AGENT_IDENTITY or has_identity_evidence:
        bootstrap = await governance.bootstrap_async(identity_evidence)
        principal_context = bootstrap.principal_context

    resolved = governance._merge_context(
        explicit_context=explicit_context,
        principal_context=principal_context,
    )
    if not governance._has_bound_principal_context(resolved):
        raise RuntimeError(
            "governed model calls require a resolved agent principal binding; "
            "provide federated identity or workflow_context with bound principal fields"
        )
    return resolved


def resolve_model_decision_context_sync(
    governance: Any,
    *,
    workflow_context: Optional[Dict[str, Any]],
    identity: Optional[ExternalIdentityEvidence],
) -> Dict[str, Any]:
    """Resolve only portable identity facts needed for a decision request."""
    explicit_context = _coerce_dict(workflow_context)
    if governance.identity_mode != FEDERATED_AGENT_IDENTITY:
        return governance._merge_context(explicit_context=explicit_context)
    if governance._has_bound_principal_context(explicit_context):
        return governance._merge_context(explicit_context=explicit_context)
    evidence = identity or ExternalIdentityEvidence()
    if not evidence.bearer_token:
        raise RuntimeError("federated model decisions require trusted identity evidence")
    bootstrap = governance.bootstrap_sync(evidence)
    return governance._merge_context(
        explicit_context=explicit_context,
        principal_context=bootstrap.principal_context,
    )


async def resolve_model_decision_context_async(
    governance: Any,
    *,
    workflow_context: Optional[Dict[str, Any]],
    identity: Optional[ExternalIdentityEvidence],
) -> Dict[str, Any]:
    explicit_context = _coerce_dict(workflow_context)
    if governance.identity_mode != FEDERATED_AGENT_IDENTITY:
        return governance._merge_context(explicit_context=explicit_context)
    if governance._has_bound_principal_context(explicit_context):
        return governance._merge_context(explicit_context=explicit_context)
    evidence = identity or ExternalIdentityEvidence()
    if not evidence.bearer_token:
        raise RuntimeError("federated model decisions require trusted identity evidence")
    bootstrap = await governance.bootstrap_async(evidence)
    return governance._merge_context(
        explicit_context=explicit_context,
        principal_context=bootstrap.principal_context,
    )


def model_decision_sync(
    governance: Any,
    *,
    request: ModelDecisionRequest,
    workflow_context: Optional[Dict[str, Any]] = None,
    identity: Optional[ExternalIdentityEvidence] = None,
) -> ModelDecision:
    context = resolve_model_decision_context_sync(
        governance, workflow_context=workflow_context, identity=identity
    )
    client, headers = governance._sync_headers(context)
    try:
        response = client.post(
            f"{governance.gateway_session.base_url}"
            f"{build_versioned_route(governance.config.api_version, _MODEL_DECISIONS_PATH)}",
            json=request.to_payload(),
            headers=headers,
        )
    except Exception as exc:
        raise PolicyTransportError("model decision transport unavailable") from exc
    payload = response.json() if response.content else {}
    if response.status_code != 200:
        try:
            governance._raise_gateway_error(response.status_code, payload)
        except PolicyViolationError:
            raise
        except Exception as exc:
            raise PolicyTransportError("model decision transport failed") from exc
    try:
        return ModelDecision.from_payload(payload)
    except ValueError as exc:
        raise PolicyTransportError("model decision response was invalid") from exc


async def model_decision_async(
    governance: Any,
    *,
    request: ModelDecisionRequest,
    workflow_context: Optional[Dict[str, Any]] = None,
    identity: Optional[ExternalIdentityEvidence] = None,
) -> ModelDecision:
    context = await resolve_model_decision_context_async(
        governance, workflow_context=workflow_context, identity=identity
    )
    session, headers = await governance._async_headers(context)
    try:
        response = await session.post(
            f"{governance.gateway_session.base_url}"
            f"{build_versioned_route(governance.config.api_version, _MODEL_DECISIONS_PATH)}",
            json=request.to_payload(),
            headers=headers,
        )
    except Exception as exc:
        raise PolicyTransportError("model decision transport unavailable") from exc
    payload = response.json() if response.content else {}
    if response.status_code != 200:
        try:
            governance._raise_gateway_error(response.status_code, payload)
        except PolicyViolationError:
            raise
        except Exception as exc:
            raise PolicyTransportError("model decision transport failed") from exc
    try:
        return ModelDecision.from_payload(payload)
    except ValueError as exc:
        raise PolicyTransportError("model decision response was invalid") from exc


def build_model_invocation_payload(
    *,
    messages: List[Dict[str, Any]],
    memory_thread_id: str,
    stream: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "messages": list(messages),
        "memory_thread_id": str(memory_thread_id).strip(),
        "stream": bool(stream),
    }
    for key in (
        "model",
        "provider",
        "response_mode",
        "max_output_tokens",
        "reasoning",
        "verbosity",
        "tool_mode",
        "tool_definitions",
        "user",
        "tool_choice",
        "parallel_tool_calls",
        "structured_output",
        "stop_sequences",
        "sampling",
        "seed",
        "metadata",
    ):
        value = kwargs.get(key)
        if value is not None:
            payload[key] = value
    return payload


def invoke_model_sync(
    governance: Any,
    *,
    workflow_context: Dict[str, Any],
    payload: Dict[str, Any],
    poll_timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 0.5,
) -> Dict[str, Any]:
    client, headers = governance._sync_headers(workflow_context)
    url = (
        f"{governance.gateway_session.base_url}"
        f"{build_versioned_route(governance.config.api_version, _MODEL_INVOCATIONS_PATH)}"
    )
    response = client.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        return response.json() if response.content else {}
    if response.status_code == 403:
        raise_forbidden_invocation_error(response.json() if response.content else {})
    if response.status_code != 202:
        detail = response.json() if response.content else {}
        raise RuntimeError(
            str(detail or f"model invocation failed with status {response.status_code}")
        )

    submission = response.json() if response.content else {}
    request_id = _normalize_optional_text(submission.get("request_id"))
    if not request_id:
        raise RuntimeError("Missing request_id in async model invocation submission response")
    poll_url = (
        f"{governance.gateway_session.base_url}"
        f"{build_versioned_route(governance.config.api_version, f'/model-invocations/responses/{request_id}')}"
    )
    deadline = time.perf_counter() + float(poll_timeout_seconds)
    while True:
        if time.perf_counter() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for model invocation result (request_id={request_id})"
            )
        poll_response = client.get(poll_url, headers=headers)
        poll_payload = poll_response.json() if poll_response.content else {}
        if poll_response.status_code == 202:
            time.sleep(max(0.1, float(poll_interval_seconds)))
            continue
        if poll_response.status_code == 200:
            result = poll_payload.get("result")
            return result if isinstance(result, dict) else {}
        if poll_response.status_code == 403:
            raise_forbidden_invocation_error(poll_payload, request_id=request_id)
        raise RuntimeError(
            str(
                poll_payload
                or f"model invocation polling failed with status {poll_response.status_code}"
            )
        )


async def invoke_model_async(
    governance: Any,
    *,
    workflow_context: Dict[str, Any],
    payload: Dict[str, Any],
    poll_timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 0.5,
) -> Dict[str, Any]:
    session, headers = await governance._async_headers(workflow_context)
    url = (
        f"{governance.gateway_session.base_url}"
        f"{build_versioned_route(governance.config.api_version, _MODEL_INVOCATIONS_PATH)}"
    )
    response = await session.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        return response.json() if response.content else {}
    if response.status_code == 403:
        raise_forbidden_invocation_error(response.json() if response.content else {})
    if response.status_code != 202:
        detail = response.json() if response.content else {}
        raise RuntimeError(
            str(detail or f"model invocation failed with status {response.status_code}")
        )

    submission = response.json() if response.content else {}
    request_id = _normalize_optional_text(submission.get("request_id"))
    if not request_id:
        raise RuntimeError("Missing request_id in async model invocation submission response")
    poll_url = (
        f"{governance.gateway_session.base_url}"
        f"{build_versioned_route(governance.config.api_version, f'/model-invocations/responses/{request_id}')}"
    )
    deadline = time.perf_counter() + float(poll_timeout_seconds)
    while True:
        if time.perf_counter() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for model invocation result (request_id={request_id})"
            )
        poll_response = await session.get(poll_url, headers=headers)
        poll_payload = poll_response.json() if poll_response.content else {}
        if poll_response.status_code == 202:
            await asyncio.sleep(max(0.1, float(poll_interval_seconds)))
            continue
        if poll_response.status_code == 200:
            result = poll_payload.get("result")
            return result if isinstance(result, dict) else {}
        if poll_response.status_code == 403:
            raise_forbidden_invocation_error(poll_payload, request_id=request_id)
        raise RuntimeError(
            str(
                poll_payload
                or f"model invocation polling failed with status {poll_response.status_code}"
            )
        )


def governed_model_call_sync(
    governance: Any,
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
    resolved_workflow_context = resolve_model_workflow_context_sync(
        governance,
        workflow_context=workflow_context,
        identity=identity,
    )
    payload = build_model_invocation_payload(
        messages=messages,
        memory_thread_id=memory_thread_id,
        stream=stream,
        **kwargs,
    )
    token = set_workflow_context(resolved_workflow_context)
    try:
        return invoke_model_sync(
            governance,
            workflow_context=resolved_workflow_context,
            payload=payload,
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    finally:
        reset_workflow_context(token)


async def governed_model_call_async(
    governance: Any,
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
    resolved_workflow_context = await resolve_model_workflow_context_async(
        governance,
        workflow_context=workflow_context,
        identity=identity,
    )
    payload = build_model_invocation_payload(
        messages=messages,
        memory_thread_id=memory_thread_id,
        stream=stream,
        **kwargs,
    )
    token = set_workflow_context(resolved_workflow_context)
    try:
        return await invoke_model_async(
            governance,
            workflow_context=resolved_workflow_context,
            payload=payload,
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    finally:
        reset_workflow_context(token)


__all__ = [
    "build_model_invocation_payload",
    "governed_model_call_async",
    "governed_model_call_sync",
    "invoke_model_async",
    "invoke_model_sync",
    "model_decision_async",
    "model_decision_sync",
    "resolve_model_decision_context_async",
    "resolve_model_decision_context_sync",
    "resolve_model_workflow_context_async",
    "resolve_model_workflow_context_sync",
]
