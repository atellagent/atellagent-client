# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Loopback-only, non-streaming OpenAI Responses API route facade.

This is not a Codex deployment: Codex requires event streaming and this facade
deliberately rejects streaming requests.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import time
from typing import Any, Mapping, Optional, Sequence
from uuid import uuid4

from atellagent_client.sdk.config import ServiceAccountConfig, load_service_account_config_from_yaml
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError

from .anthropic_facade import AnthropicMessagesFacadeRuntime, load_route_facade_capability_token


_SUPPORTED_FIELDS = {
    "input", "instructions", "max_output_tokens", "metadata", "model",
    "parallel_tool_calls", "store", "stream", "temperature", "tool_choice",
    "tools", "top_p", "user",
}


class OpenAIResponsesFacadeError(ValueError):
    """A customer-safe OpenAI-compatible error."""

    def __init__(self, message: str, *, error_type: str = "invalid_request_error", status: int = 400) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status = status


@dataclass(frozen=True)
class OpenAIResponsesFacadeResponse:
    status: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class _Invocation:
    model: str
    messages: list[dict[str, Any]]
    max_output_tokens: Optional[int]
    tools: list[dict[str, Any]]
    tool_choice: Any
    parallel_tool_calls: Optional[bool]
    sampling: dict[str, float]
    user: Optional[str]
    metadata: Optional[dict[str, Any]]


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OpenAIResponsesFacadeError(f"{field} must be an object")
    return dict(value)


def _text(value: Any, field: str, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise OpenAIResponsesFacadeError(f"{field} is required")
    return result


def _text_content(value: Any, field: str, *, allowed_types: set[str]) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or not value:
        raise OpenAIResponsesFacadeError(f"{field} must contain text")
    texts: list[str] = []
    for index, raw in enumerate(value):
        block = _object(raw, f"{field}[{index}]")
        kind = _text(block.get("type"), f"{field}[{index}].type", required=True)
        if kind not in allowed_types or set(block) - {"type", "text"} or not isinstance(block.get("text"), str):
            raise OpenAIResponsesFacadeError(f"{field}[{index}] is not a supported text block")
        texts.append(block["text"])
    return "\n".join(texts)


def _translate_input_item(raw: Any, index: int) -> list[dict[str, Any]]:
    item = _object(raw, f"input[{index}]")
    kind = item.get("type")
    if kind == "function_call":
        if set(item) - {"type", "call_id", "name", "arguments", "id", "status"}:
            raise OpenAIResponsesFacadeError(f"input[{index}] has unsupported fields")
        arguments = item.get("arguments")
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError as exc:
            raise OpenAIResponsesFacadeError(f"input[{index}].arguments must be JSON") from exc
        if not isinstance(parsed, Mapping):
            raise OpenAIResponsesFacadeError(f"input[{index}].arguments must be an object")
        return [{
            "role": "assistant", "content": "", "tool_calls": [{
                "id": _text(item.get("call_id"), f"input[{index}].call_id", required=True),
                "name": _text(item.get("name"), f"input[{index}].name", required=True),
                "arguments": dict(parsed),
            }],
        }]
    if kind == "function_call_output":
        if set(item) - {"type", "call_id", "output", "id", "status"}:
            raise OpenAIResponsesFacadeError(f"input[{index}] has unsupported fields")
        return [{
            "role": "tool",
            "tool_call_id": _text(item.get("call_id"), f"input[{index}].call_id", required=True),
            "content": _text_content(item.get("output", ""), f"input[{index}].output", allowed_types={"input_text", "output_text"}),
        }]
    if set(item) - {"role", "content", "type"}:
        raise OpenAIResponsesFacadeError(f"input[{index}] has unsupported fields")
    role = _text(item.get("role"), f"input[{index}].role", required=True)
    if role not in {"developer", "system", "user", "assistant"}:
        raise OpenAIResponsesFacadeError(f"input[{index}].role is unsupported")
    if kind not in {None, "message"}:
        raise OpenAIResponsesFacadeError(f"input[{index}].type is unsupported")
    return [{
        "role": role,
        "content": _text_content(item.get("content"), f"input[{index}].content", allowed_types={"input_text", "output_text"}),
    }]


def _translate_input(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    if not isinstance(value, list) or not value:
        raise OpenAIResponsesFacadeError("input must be a non-empty string or array")
    translated: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        translated.extend(_translate_input_item(item, index))
    return translated


def _translate_tools(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise OpenAIResponsesFacadeError("tools must be an array")
    translated: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        tool = _object(raw, f"tools[{index}]")
        if tool.get("type") != "function" or set(tool) - {"type", "name", "description", "parameters", "strict"}:
            raise OpenAIResponsesFacadeError(f"tools[{index}] is not a supported function tool")
        if not isinstance(tool.get("parameters"), Mapping):
            raise OpenAIResponsesFacadeError(f"tools[{index}].parameters must be an object")
        _text(tool.get("name"), f"tools[{index}].name", required=True)
        translated.append({
            "type": "function",
            "function": {
                key: value for key, value in tool.items() if key not in {"type", "strict"}
            },
        })
    return translated


def _translate_tool_choice(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        if value not in {"auto", "none", "required"}:
            raise OpenAIResponsesFacadeError("tool_choice is unsupported")
        return value
    choice = _object(value, "tool_choice")
    if choice.get("type") != "function" or set(choice) - {"type", "name"}:
        raise OpenAIResponsesFacadeError("tool_choice is unsupported")
    _text(choice.get("name"), "tool_choice.name", required=True)
    return {"type": "function", "function": {"name": choice["name"]}}


def _sampling_value(value: Any, field: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        raise OpenAIResponsesFacadeError(f"{field} must be a number between 0 and 1")
    return float(value)


def translate_openai_response_request(payload: Any) -> _Invocation:
    """Translate the supported non-streaming Responses API subset."""
    request = _object(payload, "request")
    unsupported = sorted(set(request) - _SUPPORTED_FIELDS)
    if unsupported:
        raise OpenAIResponsesFacadeError(f"unsupported request fields: {', '.join(unsupported)}")
    if request.get("stream") is True:
        raise OpenAIResponsesFacadeError("streaming is not supported by this route facade")
    if "stream" in request and request["stream"] is not False:
        raise OpenAIResponsesFacadeError("stream must be false when provided")
    if request.get("store") not in {None, False}:
        raise OpenAIResponsesFacadeError("store is not supported by this route facade")
    maximum = request.get("max_output_tokens")
    if maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1):
        raise OpenAIResponsesFacadeError("max_output_tokens must be a positive integer")
    metadata = request.get("metadata")
    if metadata is not None:
        metadata = _object(metadata, "metadata")
    parallel = request.get("parallel_tool_calls")
    if parallel is not None and not isinstance(parallel, bool):
        raise OpenAIResponsesFacadeError("parallel_tool_calls must be a boolean")
    sampling = {
        field: sampled for field in ("temperature", "top_p")
        if (sampled := _sampling_value(request.get(field), field)) is not None
    }
    messages = _translate_input(request.get("input"))
    instructions = request.get("instructions")
    if instructions is not None:
        messages.insert(0, {"role": "system", "content": _text(instructions, "instructions", required=True)})
    return _Invocation(
        model=_text(request.get("model"), "model", required=True), messages=messages,
        max_output_tokens=maximum, tools=_translate_tools(request.get("tools")),
        tool_choice=_translate_tool_choice(request.get("tool_choice")),
        parallel_tool_calls=parallel, sampling=sampling,
        user=_text(request.get("user"), "user") or None, metadata=metadata,
    )


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def translate_route_response(result: Mapping[str, Any], *, requested_model: str) -> dict[str, Any]:
    """Project an atomic canonical route result to a completed Response."""
    output: list[dict[str, Any]] = []
    text = result.get("output_text")
    if isinstance(text, str) and text:
        output.append({"id": f"msg_{uuid4().hex}", "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}]})
    for raw in result.get("tool_requests") or []:
        tool = raw if isinstance(raw, Mapping) else {}
        arguments = tool.get("arguments")
        if not isinstance(arguments, Mapping):
            raise OpenAIResponsesFacadeError("route returned an invalid function call", error_type="server_error", status=502)
        call_id = _text(tool.get("id"), "tool_requests.id") or f"call_{uuid4().hex}"
        output.append({"id": f"fc_{uuid4().hex}", "type": "function_call", "status": "completed", "call_id": call_id, "name": _text(tool.get("name"), "tool_requests.name", required=True), "arguments": json.dumps(dict(arguments), separators=(",", ":"))})
    usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
    input_tokens, output_tokens = _count(usage.get("input_tokens")), _count(usage.get("output_tokens"))
    response_id = _text(result.get("response_id"), "response_id")
    return {
        "id": response_id if response_id.startswith("resp_") else f"resp_{uuid4().hex}",
        "object": "response", "created_at": int(time.time()), "status": "completed",
        "error": None, "incomplete_details": None,
        "model": _text(result.get("model"), "model") or requested_model,
        "output": output,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {"input_tokens": input_tokens, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": output_tokens, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": input_tokens + output_tokens},
    }


class OpenAIResponsesFacadeRuntime(AnthropicMessagesFacadeRuntime):
    """Serve supported Responses requests on the standard enrolled loopback boundary."""

    async def handle_responses(self, payload: Any) -> OpenAIResponsesFacadeResponse:
        try:
            request = translate_openai_response_request(payload)
            result = await self.governance.governed_model_call_async(
                messages=request.messages, memory_thread_id=str(uuid4()), provider="openai",
                model=request.model, max_output_tokens=request.max_output_tokens,
                tool_definitions=request.tools or None, tool_choice=request.tool_choice,
                parallel_tool_calls=request.parallel_tool_calls, sampling=request.sampling or None,
                user=request.user, metadata=request.metadata, stream=False,
            )
            return OpenAIResponsesFacadeResponse(200, translate_route_response(result, requested_model=request.model))
        except OpenAIResponsesFacadeError as exc:
            return self._error(exc.status, exc.error_type, str(exc))
        except PolicyViolationError:
            return self._error(403, "permission_error", "Model request denied by policy")
        except PolicyTransportError:
            return self._error(503, "server_error", "Atellagent route service is unavailable")
        except Exception:
            return self._error(502, "server_error", "Atellagent route invocation failed")

    @staticmethod
    def _error(status: int, error_type: str, message: str) -> OpenAIResponsesFacadeResponse:
        return OpenAIResponsesFacadeResponse(status, {"error": {"message": message, "type": error_type, "param": None, "code": None}})

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, path, headers, payload = await self._read_request(reader)
            if method != "POST" or path != "/v1/responses":
                response = self._error(404, "invalid_request_error", "Route not found")
            elif not self._authorized(headers):
                response = self._error(401, "authentication_error", "Invalid local capability credential")
            else:
                response = await self.handle_responses(payload)
        except OpenAIResponsesFacadeError as exc:
            response = self._error(exc.status, exc.error_type, str(exc))
        except Exception:
            response = self._error(400, "invalid_request_error", "Invalid HTTP request")
        try:
            encoded = json.dumps(response.payload, separators=(",", ":")).encode("utf-8")
            writer.write((f"HTTP/1.1 {response.status} {'OK' if response.status == 200 else 'Error'}\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: {len(encoded)}\r\n\r\n").encode("ascii") + encoded)
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def _run_cli(args: argparse.Namespace) -> None:
    host, port = args.listen.rsplit(":", 1)
    runtime = OpenAIResponsesFacadeRuntime(
        load_service_account_config_from_yaml(args.config),
        capability_token=load_route_facade_capability_token(args.token_file), host=host, port=int(port),
    )
    await runtime.start()
    address = runtime.address
    print(f"OpenAI Responses route facade listening on http://{address[0]}:{address[1]}")
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run an Atellagent loopback OpenAI Responses route facade.")
    parser.add_argument("config", help="Path to the enrolled connected agent YAML")
    parser.add_argument("--token-file", required=True, help="Absolute owner-only local capability-token file")
    parser.add_argument("--listen", default="127.0.0.1:8788", help="Loopback HOST:PORT")
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run_cli(args))
    except KeyboardInterrupt:
        return
    except ValueError as exc:
        parser.error(str(exc))


__all__ = [
    "OpenAIResponsesFacadeError", "OpenAIResponsesFacadeResponse", "OpenAIResponsesFacadeRuntime",
    "main", "translate_openai_response_request", "translate_route_response",
]
