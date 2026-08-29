# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public contracts for model and filter boundary proxy runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Mapping, Optional, Protocol, runtime_checkable

from atellagent_client.protocol.context import (
    normalize_portable_workflow_context,
    serialize_portable_workflow_context,
)


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _coerce_messages(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    messages: List[Dict[str, Any]] = []
    for entry in value:
        if isinstance(entry, Mapping):
            messages.append(dict(entry))
    return messages


def _public_workflow_context(value: Any) -> Dict[str, Any]:
    normalized = normalize_portable_workflow_context(_coerce_dict(value))
    return serialize_portable_workflow_context(normalized) or {}


def _coerce_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    items: List[str] = []
    for entry in value:
        text = str(entry or "").strip()
        if text:
            items.append(text)
    return items


_MODEL_OPTION_FIELDS = {
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
    "temperature",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
}

_MODEL_RUNTIME_FIELDS = {
    "model",
    "messages",
    "memory_thread_id",
    "provider",
    "stream",
    "request_id",
    "workflow_context",
    *_MODEL_OPTION_FIELDS,
}

_FILTER_RUNTIME_FIELDS = {
    "filter_id",
    "execution_boundary",
    "content",
    "request_id",
    "workflow_context",
    "evidence",
    "metadata",
}


def _require_declared_fields(raw: Dict[str, Any], allowed: set[str], *, kind: str) -> None:
    unsupported = sorted(set(raw) - allowed)
    if unsupported:
        raise ValueError(
            f"{kind} contains unsupported fields: {', '.join(unsupported)}"
        )


@dataclass(frozen=True)
class ModelRuntimeInvocationRequest:
    model: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    memory_thread_id: Optional[str] = None
    provider: Optional[str] = None
    stream: bool = False
    request_id: Optional[str] = None
    workflow_context: Dict[str, Any] = field(default_factory=dict)
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FilterRuntimeEvaluationRequest:
    filter_id: str
    execution_boundary: str
    content: Any = None
    request_id: Optional[str] = None
    workflow_context: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ModelRuntimeHandler(Protocol):
    async def invoke_model(
        self,
        request: ModelRuntimeInvocationRequest,
    ) -> Dict[str, Any]:
        ...


@runtime_checkable
class FilterRuntimeHandler(Protocol):
    async def evaluate_filter(
        self,
        request: FilterRuntimeEvaluationRequest,
    ) -> Dict[str, Any]:
        ...


class ModelRuntimeHandlerBase:
    async def invoke_model(
        self,
        request: ModelRuntimeInvocationRequest,
    ) -> Dict[str, Any]:
        del request
        raise NotImplementedError("invoke_model is not implemented")


class FilterRuntimeHandlerBase:
    async def evaluate_filter(
        self,
        request: FilterRuntimeEvaluationRequest,
    ) -> Dict[str, Any]:
        del request
        raise NotImplementedError("evaluate_filter is not implemented")


def coerce_model_runtime_invocation_request(
    payload: Any,
) -> ModelRuntimeInvocationRequest:
    raw = _coerce_dict(payload)
    _require_declared_fields(raw, _MODEL_RUNTIME_FIELDS, kind="model runtime request")
    model = str(raw.get("model") or "").strip()
    if not model:
        raise ValueError("model is required")
    options = {
        key: raw.get(key)
        for key in _MODEL_OPTION_FIELDS
        if raw.get(key) is not None
    }
    return ModelRuntimeInvocationRequest(
        model=model,
        messages=_coerce_messages(raw.get("messages")),
        memory_thread_id=str(raw.get("memory_thread_id") or "").strip() or None,
        provider=str(raw.get("provider") or "").strip().lower() or None,
        stream=bool(raw.get("stream", False)),
        request_id=str(raw.get("request_id") or "").strip() or None,
        workflow_context=_public_workflow_context(raw.get("workflow_context")),
        options=options,
    )


def coerce_filter_runtime_evaluation_request(
    payload: Any,
) -> FilterRuntimeEvaluationRequest:
    raw = _coerce_dict(payload)
    _require_declared_fields(raw, _FILTER_RUNTIME_FIELDS, kind="filter runtime request")
    filter_id = str(raw.get("filter_id") or "").strip()
    if not filter_id:
        raise ValueError("filter_id is required")
    execution_boundary = str(raw.get("execution_boundary") or "").strip().lower()
    if execution_boundary not in {"model_boundary", "tool_response", "egress"}:
        raise ValueError(
            "execution_boundary must be model_boundary, tool_response, or egress"
        )
    return FilterRuntimeEvaluationRequest(
        filter_id=filter_id,
        execution_boundary=execution_boundary,
        content=raw.get("content"),
        request_id=str(raw.get("request_id") or "").strip() or None,
        workflow_context=_public_workflow_context(raw.get("workflow_context")),
        evidence=_coerce_dict(raw.get("evidence")),
        metadata=_coerce_dict(raw.get("metadata")),
    )


def coerce_model_runtime_result(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("model runtime handler must return a JSON object")
    result = dict(payload)
    if "output" in result and not isinstance(result["output"], list):
        result["output"] = []
    if "usage" in result and not isinstance(result["usage"], Mapping):
        result["usage"] = {}
    return result


def coerce_filter_runtime_result(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("filter runtime handler must return a JSON object")
    result = dict(payload)
    score = result.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("filter runtime handler must return a numeric score")
    score = float(score)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError("filter runtime handler score must be between 0 and 1")
    result["score"] = score
    coverage = result.get("coverage")
    if not isinstance(coverage, str) or coverage.strip().lower() != "complete":
        raise ValueError("filter runtime handler must return coverage='complete'")
    result["coverage"] = "complete"
    result["allowed"] = bool(result.get("allowed", False))
    if "violations" in result:
        result["violations"] = _coerce_text_list(result.get("violations"))
    else:
        result["violations"] = []
    if "evidence" in result and not isinstance(result["evidence"], Mapping):
        result["evidence"] = {}
    if "metadata" in result and not isinstance(result["metadata"], Mapping):
        result["metadata"] = {}
    return result


__all__ = [
    "FilterRuntimeEvaluationRequest",
    "FilterRuntimeHandler",
    "FilterRuntimeHandlerBase",
    "ModelRuntimeHandler",
    "ModelRuntimeHandlerBase",
    "ModelRuntimeInvocationRequest",
    "coerce_filter_runtime_evaluation_request",
    "coerce_filter_runtime_result",
    "coerce_model_runtime_invocation_request",
    "coerce_model_runtime_result",
]
