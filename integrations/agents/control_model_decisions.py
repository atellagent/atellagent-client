# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bounded model decision transport, response parsing, and safe errors."""

from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import uuid4

from atellagent_client.protocol.api import build_versioned_route
from atellagent_client.protocol.agent_contracts import (
    ExternalIdentityEvidence,
    ModelDecision,
    ModelDecisionRequest,
)
from atellagent_client.sdk.errors import (
    AuthenticationError,
    PolicyTransportError,
    PolicyViolationError,
)


from .control_model_context import (
    resolve_model_decision_context_sync,
    resolve_model_decision_context_async,
)

_MODEL_DECISIONS_PATH = "/model-decisions"
_MODEL_DECISION_ACTION_KEY_PREFIX = "model-decision:"


class _ModelDecisionTransportError(PolicyTransportError):
    """A model-decision failure with a safe hook-control diagnostic code."""

    def __init__(self, safe_code: str, *, http_status: int | None = None,
                 request_id: str | None = None) -> None:
        super().__init__("model decision unavailable")
        self.safe_code = safe_code
        self.http_status = http_status
        self.request_id = request_id


def _response_payload(response: Any, request_id: str) -> Dict[str, Any]:
    try:
        payload = response.json() if response.content else {}
    except (TypeError, ValueError) as exc:
        raise _ModelDecisionTransportError(
            "model_decision_gateway_response_invalid",
            http_status=response.status_code, request_id=request_id,
        ) from exc
    return payload if isinstance(payload, dict) else {}


def _gateway_status_code(status_code: int) -> str:
    if 400 <= status_code < 500:
        return "model_decision_gateway_client_status"
    if 500 <= status_code < 600:
        return "model_decision_gateway_server_status"
    return "model_decision_gateway_status"


def _response_error(response: Any, payload: dict, request_id: str) -> _ModelDecisionTransportError:
    code = _gateway_status_code(response.status_code)
    detail = payload.get("detail")
    if (response.status_code == 503 and isinstance(detail, dict)
            and detail.get("error_kind") == "model_input_capacity_exhausted"):
        code = "model_decision_capacity_exhausted"
    return _ModelDecisionTransportError(code, http_status=response.status_code,
                                        request_id=request_id)


def _model_decision_headers(
    headers: Dict[str, str],
    request: ModelDecisionRequest,
) -> Dict[str, str]:
    """Bind an idempotent decision admission to its exact public payload."""
    return {
        **headers,
        "X-Atellagent-Action-Key": (
            f"{_MODEL_DECISION_ACTION_KEY_PREFIX}{request.request_fingerprint}"
        ),
    }


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
    request_id = str(uuid4())
    try:
        response = governance._request_gateway_sync(
            "POST",
            f"{governance.gateway_session.base_url}"
            f"{build_versioned_route(governance.config.api_version, _MODEL_DECISIONS_PATH)}",
            workflow_context=context,
            json=request.to_payload(),
            request_headers=_model_decision_headers({"X-Model-Decision-Request-ID": request_id}, request),
        )
    except AuthenticationError as exc:
        raise _ModelDecisionTransportError("model_decision_gateway_auth") from exc
    except Exception as exc:
        raise _ModelDecisionTransportError("model_decision_gateway_transport") from exc
    payload = _response_payload(response, request_id)
    if response.status_code != 200:
        if response.status_code == 401:
            raise _ModelDecisionTransportError("model_decision_gateway_auth",
                                              http_status=401, request_id=request_id)
        try:
            governance._raise_gateway_error(response.status_code, payload)
        except PolicyViolationError:
            raise
        except Exception as exc:
            raise _response_error(response, payload, request_id) from exc
    try:
        return ModelDecision.from_payload(payload)
    except ValueError as exc:
        raise _ModelDecisionTransportError(
            "model_decision_gateway_response_invalid",
            http_status=response.status_code, request_id=request_id,
        ) from exc


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
    request_id = str(uuid4())
    try:
        response = await governance._request_gateway_async(
            "POST",
            f"{governance.gateway_session.base_url}"
            f"{build_versioned_route(governance.config.api_version, _MODEL_DECISIONS_PATH)}",
            workflow_context=context,
            json=request.to_payload(),
            request_headers=_model_decision_headers({"X-Model-Decision-Request-ID": request_id}, request),
        )
    except AuthenticationError as exc:
        raise _ModelDecisionTransportError("model_decision_gateway_auth") from exc
    except Exception as exc:
        raise _ModelDecisionTransportError("model_decision_gateway_transport") from exc
    payload = _response_payload(response, request_id)
    if response.status_code != 200:
        if response.status_code == 401:
            raise _ModelDecisionTransportError("model_decision_gateway_auth",
                                              http_status=401, request_id=request_id)
        try:
            governance._raise_gateway_error(response.status_code, payload)
        except PolicyViolationError:
            raise
        except Exception as exc:
            raise _response_error(response, payload, request_id) from exc
    try:
        return ModelDecision.from_payload(payload)
    except ValueError as exc:
        raise _ModelDecisionTransportError(
            "model_decision_gateway_response_invalid",
            http_status=response.status_code, request_id=request_id,
        ) from exc
