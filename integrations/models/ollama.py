# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Ollama-backed implementation of the public model-runtime handler."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .contracts import ModelRuntimeHandlerBase, ModelRuntimeInvocationRequest


def _mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def _options(request: ModelRuntimeInvocationRequest) -> Dict[str, Any]:
    options = request.options
    sampling = options.get("sampling") if isinstance(options.get("sampling"), dict) else {}
    result = {
        "temperature": options.get("temperature", sampling.get("temperature")),
        "top_p": options.get("top_p", sampling.get("top_p")),
        "seed": options.get("seed", sampling.get("seed")),
        "stop": options.get("stop_sequences", sampling.get("stop")),
    }
    return {key: value for key, value in result.items() if value is not None}


@dataclass
class OllamaModelRuntimeHandler(ModelRuntimeHandlerBase):
    """Invoke a customer-operated Ollama server through its official SDK.

    The platform performs the request and response policy checks; this handler
    only translates an admitted non-streaming invocation to Ollama's chat API.
    """

    host: Optional[str] = None
    client: Any = None
    _resolved_client: Any = field(default=None, init=False, repr=False)

    def _client(self) -> Any:
        if self.client is not None:
            return self.client
        if self._resolved_client is None:
            try:
                from ollama import AsyncClient
            except ImportError as exc:
                raise RuntimeError(
                    "Ollama model support requires: pip install 'atellagent-client[ollama]'"
                ) from exc
            self._resolved_client = (
                AsyncClient(host=self.host) if self.host else AsyncClient()
            )
        return self._resolved_client

    async def invoke_model(
        self,
        request: ModelRuntimeInvocationRequest,
    ) -> Dict[str, Any]:
        if request.stream:
            raise ValueError("Ollama streaming is coming soon")
        kwargs: Dict[str, Any] = {
            "model": request.model,
            "messages": list(request.messages),
            "stream": False,
        }
        options = _options(request)
        if options:
            kwargs["options"] = options
        tool_definitions = request.options.get("tool_definitions")
        if isinstance(tool_definitions, list):
            kwargs["tools"] = tool_definitions
        response = self._client().chat(**kwargs)
        if inspect.isawaitable(response):
            response = await response
        payload = _mapping(response)
        message = _mapping(payload.get("message"))
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        output: list[Dict[str, Any]] = []
        if text:
            output.append({"type": "output_text", "text": text})
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            output.extend(
                {"type": "tool_call", "tool_call": _mapping(item)}
                for item in tool_calls
                if _mapping(item)
            )
        usage = {
            key: payload.get(key)
            for key in ("prompt_eval_count", "eval_count")
            if payload.get(key) is not None
        }
        return {
            "provider": "ollama",
            "model": str(payload.get("model") or request.model),
            "output_text": text,
            "output": output,
            "usage": usage,
            "metadata": {
                key: payload.get(key)
                for key in ("done_reason", "created_at")
                if payload.get(key) is not None
            },
        }


__all__ = ["OllamaModelRuntimeHandler"]
