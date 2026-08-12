# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Google native function-call bridge backed by Atellagent governance.

The Google runtime is given function declarations only. This module contains
governed function calls.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Mapping

from atellagent_client.integrations.agents.capabilities import ProviderCapabilitySet

from .governed_tools import GovernedToolIngress


GOOGLE_CAPABILITIES = ProviderCapabilitySet(
    provider="google",
    sdk_nouns=("GenAI function declaration", "functionCall", "functionResponse"),
    tool_boundary_only=(
        "native function declaration publication",
        "governed function-call ingress",
        "governed function-response publication",
    ),
    model_checkpoint_aware=(),
    session_state_aware=(),
    notes=(
        "Google receives only native function declarations. Atellagent owns "
        "tool identity, policy, action state, MCP transport, and results."
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


def _function_calls(response: Any) -> list[Dict[str, Any]]:
    """Extract native Gemini functionCall parts."""
    response_data = _mapping(response)
    direct = response_data.get("function_calls") or response_data.get("functionCalls")
    if isinstance(direct, list):
        return _items(direct)
    calls: list[Dict[str, Any]] = []
    for candidate in _items(response_data.get("candidates")):
        content = _mapping(candidate.get("content"))
        for part in _items(content.get("parts")):
            call = part.get("function_call") or part.get("functionCall")
            if call:
                calls.append(_mapping(call))
    return calls


def _response_content(response: Any) -> Dict[str, Any]:
    response_data = _mapping(response)
    candidates = _items(response_data.get("candidates"))
    return _mapping(candidates[0].get("content")) if candidates else {}


@dataclass(frozen=True)
class AtellagentGoogleToolBridge:
    """Adapt Google native function calls to the canonical Atellagent ingress."""

    ingress: GovernedToolIngress

    def function_declarations(self) -> list[Dict[str, Any]]:
        return self.ingress.descriptors_for("google")

    async def execute_function_call(self, function_call: Any) -> Dict[str, Any]:
        call = _mapping(function_call)
        tool_name = str(call.get("name") or "").strip()
        call_id = str(call.get("id") or call.get("call_id") or "").strip()
        arguments = call.get("args") if "args" in call else call.get("arguments")
        if not tool_name:
            raise ValueError("Google functionCall.name is required")
        if not call_id:
            raise ValueError("Google functionCall.id is required")
        if not isinstance(arguments, Mapping):
            raise ValueError("Google functionCall.args must be an object")
        output = await self.ingress.invoke_async(
            provider_tool_name=tool_name,
            arguments=dict(arguments),
            provider_tool_call_id=call_id,
        )
        return {
            "function_response": {
                "id": call_id,
                "name": tool_name,
                "response": {"result": output},
            }
        }

    async def run_tool_loop(
        self,
        *,
        generate_content: Callable[..., Awaitable[Any] | Any],
        request: Mapping[str, Any],
        max_turns: int = 16,
    ) -> Any:
        """Run Gemini function-call turns through the governed ingress only."""
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        request_payload = dict(request)
        config = dict(request_payload.get("config") or {})
        config["tools"] = [{"function_declarations": self.function_declarations()}]
        request_payload["config"] = config
        contents = list(request_payload.get("contents") or [])
        request_payload["contents"] = contents
        response = await _await_provider_result(generate_content(**request_payload))
        for _turn in range(max_turns):
            calls = _function_calls(response)
            if not calls:
                return response
            results = [await self.execute_function_call(call) for call in calls]
            model_content = _response_content(response)
            if model_content:
                contents.append(model_content)
            contents.append(
                {
                    "role": "user",
                    "parts": results,
                }
            )
            request_payload["contents"] = contents
            response = await _await_provider_result(generate_content(**request_payload))
        raise RuntimeError("Google provider tool loop exceeded max_turns")

    async def run_with_client(
        self,
        *,
        client: Any,
        request: Mapping[str, Any],
        max_turns: int = 16,
    ) -> Any:
        """Run the loop with an official Google GenAI SDK client."""
        try:
            from google import genai  # noqa: F401 - verifies the declared extra
        except ImportError as exc:  # pragma: no cover - installation boundary
            raise RuntimeError(
                "Google support requires: pip install 'atellagent-client[google]'"
            ) from exc
        models = getattr(client, "models", None)
        generate_content = getattr(models, "generate_content", None)
        if not callable(generate_content):
            raise TypeError(
                "client must be a Google GenAI SDK client with models.generate_content"
            )
        return await self.run_tool_loop(
            generate_content=generate_content,
            request=request,
            max_turns=max_turns,
        )


def tool_bridge(*, ingress: GovernedToolIngress) -> AtellagentGoogleToolBridge:
    return AtellagentGoogleToolBridge(ingress=ingress)


__all__ = ["GOOGLE_CAPABILITIES", "AtellagentGoogleToolBridge", "tool_bridge"]
