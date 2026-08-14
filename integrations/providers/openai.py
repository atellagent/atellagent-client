# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""OpenAI native function-tool bridge backed by Atellagent governance.

This module renders governed OpenAI function tools.
The OpenAI runtime sees only function schemas; every function call is submitted
to ``GovernedToolIngress`` before any MCP transport can occur.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict, Mapping

from atellagent_client.integrations.agents.capabilities import ProviderCapabilitySet

from .governed_tools import GovernedToolIngress


OPENAI_CAPABILITIES = ProviderCapabilitySet(
    provider="openai",
    sdk_nouns=("Responses API", "function", "function_call", "function_call_output"),
    tool_boundary_only=(
        "native function-tool schema publication",
        "governed tool-call ingress",
        "governed function-call result publication",
    ),
    model_checkpoint_aware=("GovernedProviderSession decision transport",),
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

def tool_bridge(*, ingress: GovernedToolIngress) -> AtellagentOpenAIToolBridge:
    return AtellagentOpenAIToolBridge(ingress=ingress)


__all__ = [
    "OPENAI_CAPABILITIES",
    "AtellagentOpenAIToolBridge",
    "tool_bridge",
]
