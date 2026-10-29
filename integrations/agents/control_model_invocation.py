# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Model invocation payload and workflow-context lifecycle orchestration."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from atellagent_client.protocol.agent_contracts import (
    ExternalIdentityEvidence,
)
from atellagent_client.sdk.client import reset_workflow_context, set_workflow_context


from .control_model_context import (
    resolve_model_workflow_context_sync,
    resolve_model_workflow_context_async,
)
from .control_model_execution import invoke_model_sync, invoke_model_async


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
    "governed_model_call_sync",
    "governed_model_call_async",
]
