# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Runtime classes for governed OpenAI Agents model/provider integrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from atellagent_client.integrations.agents.control import ExternalAgentGovernance, ExternalIdentityEvidence
from .openai_helpers import (
    IdentityResolver,
    MemoryThreadResolver,
    coerce_openai_input_messages,
    ensure_supported_features,
    model_kwargs,
    normalize_text,
    resolved_identity,
    resolved_memory_thread_id,
)

ResponseAdapter = Callable[[Dict[str, Any]], Any]


@dataclass(frozen=True)
class AtellagentOpenAIModelGateway:
    governance: ExternalAgentGovernance
    model: str
    default_memory_thread_id: Optional[str] = None
    default_identity: Optional[ExternalIdentityEvidence] = None
    memory_thread_resolver: Optional[MemoryThreadResolver] = None
    identity_resolver: Optional[IdentityResolver] = None

    def complete_sync(
        self,
        *,
        system_instructions: Optional[str],
        input: str | Sequence[Any],
        model_settings: Any = None,
        tools: Sequence[Any] = (),
        output_schema: Any = None,
        handoffs: Sequence[Any] = (),
        tracing: Any = None,
        previous_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        prompt: Any = None,
    ) -> Dict[str, Any]:
        del tracing, previous_response_id
        ensure_supported_features(
            handoffs=handoffs,
            prompt=prompt,
        )
        messages = coerce_openai_input_messages(
            system_instructions=system_instructions,
            input_items=input,
        )
        payload = {"messages": messages, "conversation_id": conversation_id}
        memory_thread_id = resolved_memory_thread_id(
            default_memory_thread_id=self.default_memory_thread_id,
            memory_thread_resolver=self.memory_thread_resolver,
            conversation_id=conversation_id,
            model_name=self.model,
            payload=payload,
        )
        identity = resolved_identity(
            default_identity=self.default_identity,
            identity_resolver=self.identity_resolver,
            model_name=self.model,
            payload=payload,
        )
        return self.governance.governed_model_call_sync(
            messages=messages,
            memory_thread_id=memory_thread_id,
            identity=identity,
            **model_kwargs(
                model_name=self.model,
                model_settings=model_settings,
                tools=tools,
                output_schema=output_schema,
            ),
        )

    async def complete_async(
        self,
        *,
        system_instructions: Optional[str],
        input: str | Sequence[Any],
        model_settings: Any = None,
        tools: Sequence[Any] = (),
        output_schema: Any = None,
        handoffs: Sequence[Any] = (),
        tracing: Any = None,
        previous_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        prompt: Any = None,
    ) -> Dict[str, Any]:
        del tracing, previous_response_id
        ensure_supported_features(
            handoffs=handoffs,
            prompt=prompt,
        )
        messages = coerce_openai_input_messages(
            system_instructions=system_instructions,
            input_items=input,
        )
        payload = {"messages": messages, "conversation_id": conversation_id}
        memory_thread_id = resolved_memory_thread_id(
            default_memory_thread_id=self.default_memory_thread_id,
            memory_thread_resolver=self.memory_thread_resolver,
            conversation_id=conversation_id,
            model_name=self.model,
            payload=payload,
        )
        identity = resolved_identity(
            default_identity=self.default_identity,
            identity_resolver=self.identity_resolver,
            model_name=self.model,
            payload=payload,
        )
        return await self.governance.governed_model_call_async(
            messages=messages,
            memory_thread_id=memory_thread_id,
            identity=identity,
            **model_kwargs(
                model_name=self.model,
                model_settings=model_settings,
                tools=tools,
                output_schema=output_schema,
            ),
        )


@dataclass
class AtellagentOpenAIModel:
    gateway: AtellagentOpenAIModelGateway
    response_adapter: ResponseAdapter = field(default=lambda payload: payload)

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | Sequence[Any],
        model_settings: Any,
        tools: List[Any],
        output_schema: Any,
        handoffs: List[Any],
        tracing: Any,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: Any,
    ) -> Any:
        payload = await self.gateway.complete_async(
            system_instructions=system_instructions,
            input=input,
            model_settings=model_settings,
            tools=tools,
            output_schema=output_schema,
            handoffs=handoffs,
            tracing=tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        )
        return self.response_adapter(payload)

    async def close(self) -> None:
        await self.gateway.governance.close_async()

    def stream_response(
        self,
        system_instructions: str | None,
        input: str | Sequence[Any],
        model_settings: Any,
        tools: List[Any],
        output_schema: Any,
        handoffs: List[Any],
        tracing: Any,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: Any,
    ):
        del (
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id,
            conversation_id,
            prompt,
        )
        raise NotImplementedError(
            "Atellagent OpenAI model takeover does not yet support streamed model "
            "responses through the governed chat path"
        )


@dataclass(frozen=True)
class AtellagentOpenAIModelProvider:
    governance: ExternalAgentGovernance
    default_model: str
    default_memory_thread_id: Optional[str] = None
    default_identity: Optional[ExternalIdentityEvidence] = None
    memory_thread_resolver: Optional[MemoryThreadResolver] = None
    identity_resolver: Optional[IdentityResolver] = None
    response_adapter: ResponseAdapter = lambda payload: payload

    def get_model(self, model_name: Optional[str] = None) -> AtellagentOpenAIModel:
        resolved_model = normalize_text(model_name) or self.default_model
        gateway = AtellagentOpenAIModelGateway(
            governance=self.governance,
            model=resolved_model,
            default_memory_thread_id=self.default_memory_thread_id,
            default_identity=self.default_identity,
            memory_thread_resolver=self.memory_thread_resolver,
            identity_resolver=self.identity_resolver,
        )
        return AtellagentOpenAIModel(
            gateway=gateway,
            response_adapter=self.response_adapter,
        )

    async def aclose(self) -> None:
        await self.governance.close_async()


def model_gateway(
    *,
    governance: ExternalAgentGovernance,
    model: str,
    default_memory_thread_id: Optional[str] = None,
    default_identity: Optional[ExternalIdentityEvidence] = None,
    memory_thread_resolver: Optional[MemoryThreadResolver] = None,
    identity_resolver: Optional[IdentityResolver] = None,
) -> AtellagentOpenAIModelGateway:
    return AtellagentOpenAIModelGateway(
        governance=governance,
        model=str(model).strip(),
        default_memory_thread_id=default_memory_thread_id,
        default_identity=default_identity,
        memory_thread_resolver=memory_thread_resolver,
        identity_resolver=identity_resolver,
    )


def model_provider(
    *,
    governance: ExternalAgentGovernance,
    model: str,
    default_memory_thread_id: Optional[str] = None,
    default_identity: Optional[ExternalIdentityEvidence] = None,
    memory_thread_resolver: Optional[MemoryThreadResolver] = None,
    identity_resolver: Optional[IdentityResolver] = None,
    response_adapter: ResponseAdapter = lambda payload: payload,
) -> AtellagentOpenAIModelProvider:
    return AtellagentOpenAIModelProvider(
        governance=governance,
        default_model=str(model).strip(),
        default_memory_thread_id=default_memory_thread_id,
        default_identity=default_identity,
        memory_thread_resolver=memory_thread_resolver,
        identity_resolver=identity_resolver,
        response_adapter=response_adapter,
    )


__all__ = [
    "AtellagentOpenAIModel",
    "AtellagentOpenAIModelGateway",
    "AtellagentOpenAIModelProvider",
    "model_gateway",
    "model_provider",
]
