# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Anthropic Messages API tool-use bridge backed by Atellagent governance.

The Anthropic runtime receives native ``tools`` definitions and returns
``tool_use`` blocks. It never receives MCP configuration or a direct MCP
endpoint; the bridge routes each block through Atellagent's governed ingress.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

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
    model_checkpoint_aware=("GovernedProviderSession decision transport",),
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

def tool_bridge(*, ingress: GovernedToolIngress) -> AtellagentAnthropicToolBridge:
    return AtellagentAnthropicToolBridge(ingress=ingress)


__all__ = ["ANTHROPIC_CAPABILITIES", "AtellagentAnthropicToolBridge", "tool_bridge"]
