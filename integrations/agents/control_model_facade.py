# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Model governance methods exposed by the public control client."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


from atellagent_client.protocol.agent_contracts import (
    ExternalIdentityEvidence,
    ModelDecision,
    ModelDecisionRequest,
)

from . import control_model_invocation as chat
from . import control_model_context as model_context
from . import control_model_execution as model_execution
from . import control_model_decisions as model_decisions


class ControlModelMixin:
    def _resolve_model_workflow_context_sync(
        self,
        *,
        workflow_context: Optional[Dict[str, Any]],
        identity: Optional[ExternalIdentityEvidence],
    ) -> Dict[str, Any]:
        return model_context.resolve_model_workflow_context_sync(
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
        return await model_context.resolve_model_workflow_context_async(
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
        return model_execution.invoke_model_sync(
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
        return await model_execution.invoke_model_async(
            self,
            workflow_context=workflow_context,
            payload=payload,
            poll_timeout_seconds=poll_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

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

    def model_decision_sync(
        self,
        request: ModelDecisionRequest,
        *,
        workflow_context: Optional[Dict[str, Any]] = None,
        identity: Optional[ExternalIdentityEvidence] = None,
    ) -> ModelDecision:
        return model_decisions.model_decision_sync(
            self,
            request=request,
            workflow_context=workflow_context,
            identity=identity,
        )

    async def model_decision_async(
        self,
        request: ModelDecisionRequest,
        *,
        workflow_context: Optional[Dict[str, Any]] = None,
        identity: Optional[ExternalIdentityEvidence] = None,
    ) -> ModelDecision:
        return await model_decisions.model_decision_async(
            self,
            request=request,
            workflow_context=workflow_context,
            identity=identity,
        )
