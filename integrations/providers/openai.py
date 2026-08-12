# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""OpenAI native function-tool bridge backed by Atellagent governance.

This module renders governed OpenAI function tools.
The OpenAI runtime sees only function schemas; every function call is submitted
to ``GovernedToolIngress`` before any MCP transport can occur.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
import json
from typing import Any, Awaitable, Callable, Dict, Mapping

from atellagent_client.integrations.agents.capabilities import ProviderCapabilitySet

from .governed_tools import GovernedToolIngress
from .openai_models import (
    AtellagentOpenAIModel,
    AtellagentOpenAIModelGateway,
    AtellagentOpenAIModelProvider,
    model_gateway,
    model_provider,
)


OPENAI_CAPABILITIES = ProviderCapabilitySet(
    provider="openai",
    sdk_nouns=("Responses API", "function", "function_call", "function_call_output"),
    tool_boundary_only=(
        "native function-tool schema publication",
        "governed tool-call ingress",
        "governed function-call result publication",
    ),
    model_checkpoint_aware=(
        "governed model gateway",
        "governed model/provider wrapper",
    ),
    session_state_aware=(),
    notes=(
        "OpenAI receives native function schemas only. Atellagent resolves the "
        "private MCP target, authorizes the request, records the governed "
        "action, and executes the tool."
    ),
)


def _mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    for method_name in ("model_dump", "to_dict", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                candidate = method()
            except TypeError:
                candidate = method(mode="json")
            if isinstance(candidate, Mapping):
                return dict(candidate)
    return {}


def _arguments(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("OpenAI function_call arguments must be valid JSON") from exc
        if isinstance(decoded, Mapping):
            return dict(decoded)
    raise ValueError("OpenAI function_call arguments must be an object")


def _items(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [_mapping(item) for item in value]


async def _await_provider_result(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


@dataclass(frozen=True)
class AtellagentOpenAIToolBridge:
    """Adapt OpenAI native function calls to the canonical Atellagent ingress."""

    ingress: GovernedToolIngress

    def tool_definitions(self) -> list[Dict[str, Any]]:
        return self.ingress.descriptors_for("openai")

    async def execute_function_call(self, function_call: Any) -> Dict[str, Any]:
        call = _mapping(function_call)
        tool_name = str(call.get("name") or "").strip()
        call_id = str(call.get("call_id") or call.get("id") or "").strip()
        if not tool_name:
            raise ValueError("OpenAI function_call.name is required")
        if not call_id:
            raise ValueError("OpenAI function_call.call_id is required")
        output = await self.ingress.invoke_async(
            provider_tool_name=tool_name,
            arguments=_arguments(call.get("arguments")),
            provider_tool_call_id=call_id,
        )
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        }

    async def run_tool_loop(
        self,
        *,
        create_response: Callable[..., Awaitable[Any] | Any],
        request: Mapping[str, Any],
        max_turns: int = 16,
    ) -> Any:
        """Run Responses API turns while Atellagent owns every tool execution.

        ``create_response`` is normally ``client.responses.create``.  It is
        injected so credentials and the provider SDK remain outside the
        governed execution boundary.
        """
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        request_payload = dict(request)
        request_payload["tools"] = self.tool_definitions()
        response = await _await_provider_result(create_response(**request_payload))
        for _turn in range(max_turns):
            response_data = _mapping(response)
            calls = [
                item
                for item in _items(response_data.get("output"))
                if item.get("type") == "function_call"
            ]
            if not calls:
                return response
            response_id = str(response_data.get("id") or "").strip()
            if not response_id:
                raise ValueError("OpenAI tool response must include an id")
            outputs = [await self.execute_function_call(call) for call in calls]
            response = await _await_provider_result(
                create_response(
                    previous_response_id=response_id,
                    input=outputs,
                    tools=self.tool_definitions(),
                )
            )
        raise RuntimeError("OpenAI provider tool loop exceeded max_turns")

    async def run_with_client(
        self,
        *,
        client: Any,
        request: Mapping[str, Any],
        max_turns: int = 16,
    ) -> Any:
        """Run the loop with an official OpenAI SDK client.

        The lower-level ``run_tool_loop`` remains available for advanced
        customer wrappers.  This entry point is the supported direct
        integration and deliberately keeps the API key on the customer side.
        """
        try:
            import openai  # noqa: F401 - verifies the declared integration extra
        except ImportError as exc:  # pragma: no cover - installation boundary
            raise RuntimeError(
                "OpenAI support requires: pip install 'atellagent-client[openai]'"
            ) from exc
        responses = getattr(client, "responses", None)
        create_response = getattr(responses, "create", None)
        if not callable(create_response):
            raise TypeError("client must be an OpenAI SDK client with responses.create")
        return await self.run_tool_loop(
            create_response=create_response,
            request=request,
            max_turns=max_turns,
        )


def tool_bridge(*, ingress: GovernedToolIngress) -> AtellagentOpenAIToolBridge:
    return AtellagentOpenAIToolBridge(ingress=ingress)


__all__ = [
    "OPENAI_CAPABILITIES",
    "AtellagentOpenAIToolBridge",
    "AtellagentOpenAIModel",
    "AtellagentOpenAIModelGateway",
    "AtellagentOpenAIModelProvider",
    "model_gateway",
    "model_provider",
    "tool_bridge",
]
