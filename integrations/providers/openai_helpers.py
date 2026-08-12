# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Helper functions for governed OpenAI Agents model/provider integrations."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence

from atellagent_client.sdk.client import get_workflow_context

from atellagent_client.integrations.agents.control import ExternalIdentityEvidence

MemoryThreadResolver = Callable[..., Optional[str]]
IdentityResolver = Callable[..., Optional[ExternalIdentityEvidence]]


def normalize_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def is_uuid_text(value: Any) -> bool:
    candidate = normalize_text(value)
    if not candidate:
        return False
    try:
        uuid.UUID(candidate)
    except ValueError:
        return False
    return True


def coerce_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def coerce_sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def message_part_text(part: Any) -> Optional[str]:
    if isinstance(part, str):
        return part.strip() or None
    if not isinstance(part, dict):
        return None
    for key in ("text", "content", "value"):
        value = normalize_text(part.get(key))
        if value:
            return value
    part_type = normalize_text(part.get("type"))
    if part_type in {"input_text", "output_text"}:
        text = normalize_text(part.get("text"))
        if text:
            return text
    return None


def message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = [message_part_text(part) for part in coerce_sequence(content)]
    text_parts = [part for part in parts if part]
    return "\n".join(text_parts).strip()


def coerce_openai_input_messages(
    *,
    system_instructions: Optional[str],
    input_items: str | Sequence[Any],
) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    if normalize_text(system_instructions):
        messages.append({"role": "system", "content": str(system_instructions)})
    if isinstance(input_items, str):
        messages.append({"role": "user", "content": input_items})
        return messages

    for item in input_items:
        if isinstance(item, str):
            text = item.strip()
            if text:
                messages.append({"role": "user", "content": text})
            continue
        if not isinstance(item, dict):
            continue
        item_type = normalize_text(item.get("type"))
        role = normalize_text(item.get("role")) or "user"
        if item_type in {"message", "input_message", "output_message", None}:
            content = message_content_text(item.get("content"))
            if content:
                messages.append({"role": role, "content": content})
            continue
        if item_type in {"input_text", "output_text"}:
            content = normalize_text(item.get("text"))
            if content:
                messages.append(
                    {
                        "role": "assistant" if item_type == "output_text" else role,
                        "content": content,
                    }
                )
    if not messages:
        raise RuntimeError("OpenAI governed model call requires text-bearing input")
    return messages


def serialize_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize_value(item) for item in value]
    for method_name in ("model_dump", "to_dict", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                dumped = method()
            except TypeError:
                try:
                    dumped = method(mode="json", exclude_none=True)
                except Exception:
                    continue
            except Exception:
                continue
            if isinstance(dumped, dict):
                return serialize_value(dumped)
    if hasattr(value, "__dict__"):
        return serialize_value(vars(value))
    return str(value)


def serialize_tools(tools: Sequence[Any]) -> Optional[List[Dict[str, Any]]]:
    serialized: List[Dict[str, Any]] = []
    for tool in tools:
        payload = serialize_value(tool)
        if isinstance(payload, dict):
            serialized.append(payload)
    return serialized or None


def setting(model_settings: Any, key: str) -> Any:
    if isinstance(model_settings, dict):
        return model_settings.get(key)
    return getattr(model_settings, key, None)


def model_kwargs(
    *,
    model_name: str,
    model_settings: Any,
    tools: Sequence[Any],
    output_schema: Any = None,
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {"model": model_name}
    provider = normalize_text(setting(model_settings, "provider"))
    if provider:
        kwargs["provider"] = provider
    sampling: Dict[str, Any] = {}
    for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
        value = setting(model_settings, key)
        if value is not None:
            sampling[key] = value
    if sampling:
        kwargs["sampling"] = sampling
    max_tokens = setting(model_settings, "max_tokens")
    if max_tokens is not None:
        kwargs["max_output_tokens"] = max_tokens
    for key in ("tool_choice", "parallel_tool_calls", "user"):
        value = setting(model_settings, key)
        if value is not None:
            kwargs[key] = value
    structured_output = compile_response_format(output_schema)
    if structured_output is not None:
        if provider and provider != "openai":
            raise NotImplementedError(
                "Atellagent OpenAI model takeover only compiles output_schema "
                "when the governed provider path resolves to OpenAI-compatible "
                "structured output"
            )
        kwargs["response_mode"] = "structured_output"
        kwargs["structured_output"] = structured_output
    serialized_tools = serialize_tools(tools)
    if serialized_tools:
        kwargs["tool_definitions"] = serialized_tools
    return kwargs


def resolved_memory_thread_id(
    *,
    default_memory_thread_id: Optional[str],
    memory_thread_resolver: Optional[MemoryThreadResolver],
    conversation_id: Optional[str],
    model_name: str,
    payload: Dict[str, Any],
) -> str:
    runtime_context = coerce_mapping(get_workflow_context())
    resolved = None
    if memory_thread_resolver is not None:
        resolved = normalize_text(
            memory_thread_resolver(
                workflow_context=runtime_context,
                conversation_id=conversation_id,
                model=model_name,
                payload=payload,
            )
        )
    if not resolved:
        candidate = runtime_context.get("memory_thread_id")
        if is_uuid_text(candidate):
            resolved = str(candidate).strip()
    if not resolved and is_uuid_text(default_memory_thread_id):
        resolved = str(default_memory_thread_id).strip()
    if not resolved and is_uuid_text(conversation_id):
        resolved = str(conversation_id).strip()
    if not resolved:
        raise RuntimeError(
            "OpenAI governed model calls require memory_thread_id via runtime workflow "
            "context, model configuration, or explicit resolver"
        )
    return resolved


def resolved_identity(
    *,
    default_identity: Optional[ExternalIdentityEvidence],
    identity_resolver: Optional[IdentityResolver],
    model_name: str,
    payload: Dict[str, Any],
) -> Optional[ExternalIdentityEvidence]:
    if identity_resolver is None:
        return default_identity
    resolved = identity_resolver(model=model_name, payload=payload)
    return resolved or default_identity


def compile_response_format(output_schema: Any) -> Optional[Dict[str, Any]]:
    if output_schema is None:
        return None
    serialized = serialize_value(output_schema)
    if isinstance(serialized, dict):
        format_type = normalize_text(serialized.get("type"))
        if format_type in {"json_schema", "json_object", "text"}:
            return serialized
        if "json_schema" in serialized and isinstance(serialized.get("json_schema"), dict):
            return {
                "type": "json_schema",
                "json_schema": serialize_value(serialized.get("json_schema")),
            }
        schema_name = normalize_text(
            serialized.get("name")
            or serialized.get("title")
            or getattr(output_schema, "__name__", None)
        ) or "structured_output"
        strict = serialized.get("strict")
        wrapper = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "schema": serialized,
            },
        }
        if strict is not None:
            wrapper["json_schema"]["strict"] = bool(strict)
        return wrapper
    raise TypeError(
        "output_schema must be a JSON-schema mapping, a response_format mapping, "
        "or a schema-bearing object"
    )


def ensure_supported_features(
    *,
    handoffs: Sequence[Any],
    prompt: Any,
) -> None:
    if handoffs:
        raise NotImplementedError(
            "Atellagent OpenAI model takeover does not yet compile handoffs "
            "into the governed model invocation path"
        )
    if prompt is not None:
        raise NotImplementedError(
            "Atellagent OpenAI model takeover does not yet compile prompt config "
            "into the governed chat-completions path"
        )


__all__ = [
    "IdentityResolver",
    "MemoryThreadResolver",
    "compile_response_format",
    "coerce_openai_input_messages",
    "ensure_supported_features",
    "model_kwargs",
    "normalize_text",
    "resolved_identity",
    "resolved_memory_thread_id",
]
