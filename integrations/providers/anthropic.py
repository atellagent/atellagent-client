# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Anthropic Messages API tool-use bridge backed by Atellagent governance.

The Anthropic runtime receives native ``tools`` definitions and returns
``tool_use`` blocks. It never receives MCP configuration or a direct MCP
endpoint; the bridge routes each block through Atellagent's governed ingress.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Mapping

from atellagent_client.integrations.agents.capabilities import ProviderCapabilitySet

from .governed_tools import GovernedToolIngress


ANTHROPIC_CAPABILITIES = ProviderCapabilitySet(
    provider="anthropic",
    sdk_nouns=("Messages API", "tool_use", "tool_result"),
    tool_boundary_only=(
        "native tool definition publication",
        "governed tool-use ingress",
        "governed tool-result publication",
    ),
    model_checkpoint_aware=(),
    session_state_aware=(),
    notes=(
        "Anthropic receives native Messages API tools only. Atellagent owns "
        "action identity, authorization, state, MCP transport, and results."
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


def _items(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [_mapping(item) for item in value]


async def _await_provider_result(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


@dataclass(frozen=True)
class AtellagentAnthropicToolBridge:
    """Adapt Anthropic Messages ``tool_use`` blocks to Atellagent invocations."""

    ingress: GovernedToolIngress

    def tool_definitions(self) -> list[Dict[str, Any]]:
        return self.ingress.descriptors_for("anthropic")

    async def execute_tool_use(self, tool_use: Any) -> Dict[str, Any]:
        call = _mapping(tool_use)
        tool_name = str(call.get("name") or "").strip()
        call_id = str(call.get("id") or "").strip()
        arguments = call.get("input")
        if call.get("type") not in {None, "tool_use"}:
            raise ValueError("Anthropic tool block must have type 'tool_use'")
        if not tool_name:
            raise ValueError("Anthropic tool_use.name is required")
        if not call_id:
            raise ValueError("Anthropic tool_use.id is required")
        if not isinstance(arguments, Mapping):
            raise ValueError("Anthropic tool_use.input must be an object")
        output = await self.ingress.invoke_async(
            provider_tool_name=tool_name,
            arguments=dict(arguments),
            provider_tool_call_id=call_id,
        )
        return {
            "type": "tool_result",
            "tool_use_id": call_id,
            "content": output,
        }

    async def run_tool_loop(
        self,
        *,
        create_message: Callable[..., Awaitable[Any] | Any],
        request: Mapping[str, Any],
        max_turns: int = 16,
    ) -> Any:
        """Run Messages API turns while dispatching tool use only via Atellagent."""
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        request_payload = dict(request)
        request_payload["tools"] = self.tool_definitions()
        messages = list(request_payload.get("messages") or [])
        request_payload["messages"] = messages
        response = await _await_provider_result(create_message(**request_payload))
        for _turn in range(max_turns):
            response_data = _mapping(response)
            content = _items(response_data.get("content"))
            calls = [item for item in content if item.get("type") == "tool_use"]
            if not calls:
                return response
            results = [await self.execute_tool_use(call) for call in calls]
            messages = [
                *messages,
                {"role": "assistant", "content": content},
                {"role": "user", "content": results},
            ]
            request_payload["messages"] = messages
            response = await _await_provider_result(create_message(**request_payload))
        raise RuntimeError("Anthropic provider tool loop exceeded max_turns")

    async def run_with_client(
        self,
        *,
        client: Any,
        request: Mapping[str, Any],
        max_turns: int = 16,
    ) -> Any:
        """Run the loop with an official Anthropic SDK client."""
        try:
            import anthropic  # noqa: F401 - verifies the declared integration extra
        except ImportError as exc:  # pragma: no cover - installation boundary
            raise RuntimeError(
                "Anthropic support requires: pip install 'atellagent-client[anthropic]'"
            ) from exc
        messages = getattr(client, "messages", None)
        create_message = getattr(messages, "create", None)
        if not callable(create_message):
            raise TypeError("client must be an Anthropic SDK client with messages.create")
        return await self.run_tool_loop(
            create_message=create_message,
            request=request,
            max_turns=max_turns,
        )


def tool_bridge(*, ingress: GovernedToolIngress) -> AtellagentAnthropicToolBridge:
    return AtellagentAnthropicToolBridge(ingress=ingress)


__all__ = ["ANTHROPIC_CAPABILITIES", "AtellagentAnthropicToolBridge", "tool_bridge"]
