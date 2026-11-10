# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Google native function-call bridge backed by Atellagent governance.

The Google runtime is given function declarations only. This module contains
governed function calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

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
    model_checkpoint_aware=("GovernedProviderSession decision transport",),
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

def tool_bridge(*, ingress: GovernedToolIngress) -> AtellagentGoogleToolBridge:
    return AtellagentGoogleToolBridge(ingress=ingress)


__all__ = ["GOOGLE_CAPABILITIES", "AtellagentGoogleToolBridge", "tool_bridge"]
