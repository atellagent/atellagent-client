# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Provider-neutral function-tool contracts.

Providers receive only the public function schema. The local target binding,
actual tool name, credentials, and transport remain outside provider schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Dict, Iterable, Mapping, Optional

from atellagent_client.sdk.client import AtellagentClient


_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


def _normalized_text(value: Any, *, field_name: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError(f"{field_name} is required")
    return candidate


def _normalized_mapping(value: Any, *, field_name: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _schema_digest(schema: Mapping[str, Any]) -> str:
    """Return the canonical digest for a provider-visible schema."""
    encoded = json.dumps(
        dict(schema),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class GovernedToolDescriptor:
    """One provider-visible function bound to an opaque local target."""

    name: str
    description: str
    input_schema: Dict[str, Any]
    target_binding: str
    target_tool_name: str
    tool_id: Optional[str] = None
    policy_metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = _normalized_text(self.name, field_name="name")
        if not _TOOL_NAME_PATTERN.fullmatch(name):
            raise ValueError("name must be a provider-safe function identifier")
        schema = _normalized_mapping(self.input_schema, field_name="input_schema")
        if schema.get("type") != "object":
            raise ValueError("input_schema.type must be 'object'")
        if not isinstance(schema.get("properties", {}), Mapping):
            raise ValueError("input_schema.properties must be an object")
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(
            isinstance(item, str) and item.strip() for item in required
        ):
            raise ValueError("input_schema.required must contain non-empty property names")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", _normalized_text(self.description, field_name="description"))
        object.__setattr__(self, "input_schema", schema)
        object.__setattr__(
            self,
            "target_binding",
            _normalized_text(self.target_binding, field_name="target_binding"),
        )
        object.__setattr__(
            self,
            "target_tool_name",
            _normalized_text(self.target_tool_name, field_name="target_tool_name"),
        )
        object.__setattr__(self, "tool_id", str(self.tool_id).strip() or None if self.tool_id is not None else None)
        object.__setattr__(self, "policy_metadata", dict(self.policy_metadata or {}))

    def to_openai_tool(self) -> Dict[str, Any]:
        """Return an OpenAI native function-tool definition without routing facts."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.input_schema),
            "strict": True,
        }

    def to_anthropic_tool(self) -> Dict[str, Any]:
        """Return an Anthropic Messages API tool definition without routing facts."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
        }

    def to_google_tool(self) -> Dict[str, Any]:
        """Return a Google GenAI native function declaration without routing facts."""
        return {
            "name": self.name,
            "description": self.description,
            "parametersJsonSchema": dict(self.input_schema),
        }


@dataclass
class GovernedToolIngress:
    """Provider-tool invocation bridge."""

    client: AtellagentClient
    descriptors: Iterable[GovernedToolDescriptor]
    source_agent: Optional[str] = None
    workflow_context: Optional[Dict[str, Any]] = None
    provider: str = "unknown"

    def __post_init__(self) -> None:
        catalog: Dict[str, GovernedToolDescriptor] = {}
        for descriptor in self.descriptors:
            if not isinstance(descriptor, GovernedToolDescriptor):
                raise TypeError("descriptors must contain GovernedToolDescriptor values")
            if descriptor.name in catalog:
                raise ValueError(f"duplicate governed provider tool: {descriptor.name}")
            catalog[descriptor.name] = descriptor
        if not catalog:
            raise ValueError("at least one governed tool descriptor is required")
        self._catalog = catalog
        self.provider = _normalized_text(self.provider, field_name="provider")
        self.workflow_context = dict(self.workflow_context or {})

    def descriptor(self, provider_tool_name: str) -> GovernedToolDescriptor:
        name = _normalized_text(provider_tool_name, field_name="provider_tool_name")
        try:
            return self._catalog[name]
        except KeyError as exc:
            raise ValueError(f"unknown governed provider tool: {name}") from exc

    def descriptors_for(self, provider: str) -> list[Dict[str, Any]]:
        normalized_provider = _normalized_text(provider, field_name="provider").lower()
        renderer = {
            "openai": GovernedToolDescriptor.to_openai_tool,
            "anthropic": GovernedToolDescriptor.to_anthropic_tool,
            "google": GovernedToolDescriptor.to_google_tool,
        }.get(normalized_provider)
        if renderer is None:
            raise ValueError(f"unsupported provider tool format: {normalized_provider}")
        return [renderer(descriptor) for descriptor in self._catalog.values()]

    def _action_context(
        self,
        *,
        descriptor: GovernedToolDescriptor,
        provider_tool_call_id: str,
    ) -> Dict[str, Any]:
        return {
            "provider_tool": {
                "schema_version": "atellagent.provider-tool-bridge.v2",
                "provider": self.provider,
                "provider_tool_name": descriptor.name,
                "provider_tool_call_id": provider_tool_call_id,
                "tool_id": descriptor.tool_id,
                "target_input_schema_sha256": _schema_digest(descriptor.input_schema),
            },
            "policy_metadata": dict(descriptor.policy_metadata),
        }

    def invoke_sync(
        self,
        *,
        provider_tool_name: str,
        arguments: Mapping[str, Any],
        provider_tool_call_id: str,
    ) -> str:
        descriptor = self.descriptor(provider_tool_name)
        call_id = _normalized_text(provider_tool_call_id, field_name="provider_tool_call_id")
        return self.client.call_mcp_tool(
            descriptor.target_binding,
            descriptor.target_tool_name,
            _normalized_mapping(arguments, field_name="arguments"),
            workflow_context=dict(self.workflow_context or {}),
            source_agent=self.source_agent,
            tool_call_id=call_id,
            action_context=self._action_context(
                descriptor=descriptor,
                provider_tool_call_id=call_id,
            ),
        )

    async def invoke_async(
        self,
        *,
        provider_tool_name: str,
        arguments: Mapping[str, Any],
        provider_tool_call_id: str,
    ) -> str:
        descriptor = self.descriptor(provider_tool_name)
        call_id = _normalized_text(provider_tool_call_id, field_name="provider_tool_call_id")
        return await self.client.call_mcp_tool_async(
            descriptor.target_binding,
            descriptor.target_tool_name,
            _normalized_mapping(arguments, field_name="arguments"),
            workflow_context=dict(self.workflow_context or {}),
            source_agent=self.source_agent,
            tool_call_id=call_id,
            action_context=self._action_context(
                descriptor=descriptor,
                provider_tool_call_id=call_id,
            ),
        )


__all__ = ["GovernedToolDescriptor", "GovernedToolIngress"]
