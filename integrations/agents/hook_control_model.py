# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Governed model-decision dispatch for a native hook controller."""

from __future__ import annotations

from typing import Any, Dict, Mapping

from atellagent_client.protocol.agent_contracts import ModelDecisionRequest
from .hook_control_helpers import _messages, _object
from .hook_control_protocol import HookControlError


async def model_decision(runtime, raw_params: Any) -> Dict[str, Any]:
    params = _object(
        raw_params,
        "params",
        allowed={
            "host",
            "session_id",
            "turn_id",
            "input_scope",
            "messages",
            "model",
            "provider",
            "provider_request",
        },
    )
    runtime._turn_fields(params, include_tool=False)
    input_scope = str(params.get("input_scope") or "turn_entry").strip()
    if input_scope not in {"turn_entry", "full_model_request"}:
        raise HookControlError("invalid_input_scope")
    provider_request = params.get("provider_request")
    if provider_request is not None and not isinstance(provider_request, Mapping):
        raise HookControlError("invalid_provider_request")
    decision_request = ModelDecisionRequest(
        input_scope=input_scope,  # type: ignore[arg-type]
        messages=_messages(params.get("messages")),
        model=(str(params["model"]) if params.get("model") is not None else None),
        provider=(
            str(params["provider"])
            if params.get("provider") is not None
            else None
        ),
        provider_request=(dict(provider_request) if provider_request else None),
    )
    decision = await runtime.governance.model_decision_async(decision_request)
    if (
        decision.input_scope != decision_request.input_scope
        or decision.request_fingerprint != decision_request.request_fingerprint
    ):
        raise HookControlError("decision_binding_invalid")
    if decision.obligations:
        raise HookControlError("unsupported_obligation")
    return {
        "allowed": decision.outcome == "allow",
        "enforcement": decision.enforcement,
        "input_scope": decision.input_scope,
        "reason_code": decision.reason_code,
        "reason": decision.reason,
        "decision_id": decision.decision_id,
        "correlation_id": decision.correlation_id,
    }
