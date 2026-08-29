# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Loopback-only, non-streaming Anthropic Messages route facade.

The facade owns no provider credential and never relays a caller authorization
header. The enrolled Atellagent boundary is its only route authority.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import hmac
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping, Optional, Sequence
from uuid import uuid4

from atellagent_client.sdk.config import ServiceAccountConfig, load_service_account_config_from_yaml
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError

from .control import ExternalAgentGovernance
from .identity_mode import BOUNDARY_IDENTITY_ONLY


_MAX_HEADER_BYTES = 32 * 1024
_MAX_BODY_BYTES = 1024 * 1024
_MAX_MESSAGES = 100
_LOCAL_HOSTS = {"127.0.0.1", "::1"}
_CONTROL_CAPABILITY = "agent.control"
_SUPPORTED_FIELDS = {
    "max_tokens", "messages", "metadata", "model", "stop_sequences", "stream",
    "system", "temperature", "tool_choice", "tools", "top_p",
}


class AnthropicFacadeError(ValueError):
    """A customer-safe Anthropic-compatible error."""

    def __init__(self, message: str, *, error_type: str = "invalid_request_error", status: int = 400) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status = status


@dataclass(frozen=True)
class AnthropicFacadeResponse:
    status: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class _Invocation:
    model: str
    messages: list[dict[str, Any]]
    max_output_tokens: int
    tools: list[dict[str, Any]]
    tool_choice: Any
    stop_sequences: list[str]
    sampling: dict[str, float]
    user: Optional[str]


def _text(value: Any, field: str, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise AnthropicFacadeError(f"{field} is required")
    return result


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AnthropicFacadeError(f"{field} must be an object")
    return dict(value)


def _text_content(value: Any, field: str) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise AnthropicFacadeError(f"{field} must contain text")
    texts: list[str] = []
    for index, raw in enumerate(value):
        block = _object(raw, f"{field}[{index}]")
        if set(block) - {"type", "text"} or block.get("type") != "text" or not isinstance(block.get("text"), str):
            raise AnthropicFacadeError(f"{field}[{index}] is not a supported text block")
        texts.append(block["text"])
    return "\n".join(texts)


def _translate_message(raw: Any, index: int) -> list[dict[str, Any]]:
    message = _object(raw, f"messages[{index}]")
    if set(message) - {"role", "content"}:
        raise AnthropicFacadeError(f"messages[{index}] has unsupported fields")
    role = _text(message.get("role"), f"messages[{index}].role", required=True)
    if role not in {"user", "assistant"}:
        raise AnthropicFacadeError(f"messages[{index}].role must be user or assistant")
    content = message.get("content")
    if isinstance(content, str):
        return [{"role": role, "content": content}]
    if not isinstance(content, list) or not content:
        raise AnthropicFacadeError(f"messages[{index}].content must be non-empty")
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    for block_index, raw_block in enumerate(content):
        block = _object(raw_block, f"messages[{index}].content[{block_index}]")
        kind = _text(block.get("type"), f"messages[{index}].content[{block_index}].type", required=True)
        prefix = f"messages[{index}].content[{block_index}]"
        if kind == "text":
            if set(block) - {"type", "text"} or not isinstance(block.get("text"), str):
                raise AnthropicFacadeError(f"{prefix} is invalid")
            text_parts.append(block["text"])
        elif kind == "tool_use":
            if role != "assistant" or set(block) - {"type", "id", "name", "input"}:
                raise AnthropicFacadeError(f"{prefix} is invalid")
            arguments = block.get("input")
            if not isinstance(arguments, Mapping):
                raise AnthropicFacadeError(f"{prefix}.input must be an object")
            tool_calls.append({
                "id": _text(block.get("id"), f"{prefix}.id", required=True),
                "name": _text(block.get("name"), f"{prefix}.name", required=True),
                "arguments": dict(arguments),
            })
        elif kind == "tool_result":
            if role != "user" or set(block) - {"type", "tool_use_id", "content", "is_error"}:
                raise AnthropicFacadeError(f"{prefix} is invalid")
            tool_results.append({
                "role": "tool",
                "tool_call_id": _text(block.get("tool_use_id"), f"{prefix}.tool_use_id", required=True),
                "content": _text_content(block.get("content", ""), f"{prefix}.content"),
            })
        else:
            raise AnthropicFacadeError(f"{prefix} has an unsupported block type")
    translated: list[dict[str, Any]] = []
    if text_parts or tool_calls:
        current: dict[str, Any] = {"role": role, "content": "\n".join(text_parts)}
        if tool_calls:
            current["tool_calls"] = tool_calls
        translated.append(current)
    translated.extend(tool_results)
    if not translated:
        raise AnthropicFacadeError(f"messages[{index}].content has no supported blocks")
    return translated


def _tools(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AnthropicFacadeError("tools must be an array")
    translated: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        tool = _object(raw, f"tools[{index}]")
        if set(tool) - {"name", "description", "input_schema"}:
            raise AnthropicFacadeError(f"tools[{index}] has unsupported fields")
        _text(tool.get("name"), f"tools[{index}].name", required=True)
        if not isinstance(tool.get("input_schema"), Mapping):
            raise AnthropicFacadeError(f"tools[{index}].input_schema must be an object")
        translated.append(tool)
    return translated


def _tool_choice(value: Any) -> Any:
    if value is None:
        return None
    choice = _object(value, "tool_choice")
    kind = _text(choice.get("type"), "tool_choice.type", required=True)
    expected_fields = {"type"} if kind in {"auto", "any"} else {"type", "name"}
    if kind not in {"auto", "any", "tool"} or set(choice) - expected_fields:
        raise AnthropicFacadeError("tool_choice is unsupported")
    if kind == "tool":
        _text(choice.get("name"), "tool_choice.name", required=True)
    return choice


def _sampling_value(value: Any, field: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        raise AnthropicFacadeError(f"{field} must be a number between 0 and 1")
    return float(value)


def translate_anthropic_request(payload: Any) -> _Invocation:
    """Translate the supported non-streaming Messages API subset."""
    request = _object(payload, "request")
    unsupported = sorted(set(request) - _SUPPORTED_FIELDS)
    if unsupported:
        raise AnthropicFacadeError(f"unsupported request fields: {', '.join(unsupported)}")
    if request.get("stream") is True:
        raise AnthropicFacadeError("streaming is not supported by this route facade")
    if "stream" in request and request["stream"] is not False:
        raise AnthropicFacadeError("stream must be false when provided")
    max_tokens = request.get("max_tokens")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
        raise AnthropicFacadeError("max_tokens must be a positive integer")
    raw_messages = request.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages or len(raw_messages) > _MAX_MESSAGES:
        raise AnthropicFacadeError(f"messages must contain between 1 and {_MAX_MESSAGES} items")
    messages: list[dict[str, Any]] = []
    if request.get("system") is not None:
        messages.append({"role": "system", "content": _text_content(request["system"], "system")})
    for index, message in enumerate(raw_messages):
        messages.extend(_translate_message(message, index))
    metadata = request.get("metadata")
    user: Optional[str] = None
    if metadata is not None:
        metadata = _object(metadata, "metadata")
        if set(metadata) - {"user_id"}:
            raise AnthropicFacadeError("metadata has unsupported fields")
        user = _text(metadata.get("user_id"), "metadata.user_id") or None
    stops = request.get("stop_sequences", [])
    if not isinstance(stops, list) or not all(isinstance(item, str) for item in stops):
        raise AnthropicFacadeError("stop_sequences must be an array of strings")
    sampling = {
        field: value for field in ("temperature", "top_p")
        if (value := _sampling_value(request.get(field), field)) is not None
    }
    return _Invocation(
        model=_text(request.get("model"), "model", required=True),
        messages=messages,
        max_output_tokens=max_tokens,
        tools=_tools(request.get("tools")),
        tool_choice=_tool_choice(request.get("tool_choice")),
        stop_sequences=list(stops),
        sampling=sampling,
        user=user,
    )


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def translate_route_response(result: Mapping[str, Any], *, requested_model: str) -> dict[str, Any]:
    """Project an atomic canonical route result to an Anthropic Message."""
    content: list[dict[str, Any]] = []
    if isinstance(result.get("output_text"), str) and result["output_text"]:
        content.append({"type": "text", "text": result["output_text"]})
    for index, raw in enumerate(result.get("tool_requests") or []):
        tool = raw if isinstance(raw, Mapping) else {}
        arguments = tool.get("arguments")
        if not isinstance(arguments, Mapping):
            raise AnthropicFacadeError("route returned an invalid tool request", error_type="api_error", status=502)
        name = _text(tool.get("name"), f"tool_requests[{index}].name", required=True)
        identifier = _text(tool.get("id"), f"tool_requests[{index}].id") or f"toolu_{uuid4().hex}"
        content.append({"type": "tool_use", "id": identifier, "name": name, "input": dict(arguments)})
    usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
    finish = _text(result.get("finish_reason"), "finish_reason").lower()
    stop_reason = "tool_use" if any(item["type"] == "tool_use" for item in content) else {
        "length": "max_tokens", "max_output_tokens": "max_tokens", "stop_sequence": "stop_sequence",
    }.get(finish, "end_turn")
    response_id = _text(result.get("response_id"), "response_id")
    return {
        "id": response_id if response_id.startswith("msg_") else f"msg_{uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": _text(result.get("model"), "model") or requested_model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": _nonnegative_int(usage.get("input_tokens")), "output_tokens": _nonnegative_int(usage.get("output_tokens"))},
    }


def load_route_facade_capability_token(path: str) -> str:
    """Load an owner-only local capability credential without logging it."""
    token_path = Path(path).expanduser()
    if not token_path.is_absolute() or token_path.is_symlink():
        raise ValueError("route facade token file must be an absolute regular file")
    try:
        details = token_path.stat()
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("route facade token file is unavailable") from exc
    if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077:
        raise ValueError("route facade token file must be owner-only")
    if len(token) < 32 or len(token) > 512:
        raise ValueError("route facade token must contain 32 to 512 characters")
    return token


class AnthropicMessagesFacadeRuntime:
    """Serve supported Messages requests on an enrolled loopback boundary."""

    def __init__(self, config: ServiceAccountConfig, *, capability_token: str, host: str = "127.0.0.1", port: int = 8787) -> None:
        if config.integration_type != "agent" or config.identity_mode != BOUNDARY_IDENTITY_ONLY:
            raise ValueError("Anthropic route facade requires a boundary-only connected agent")
        if set(config.capabilities) != {_CONTROL_CAPABILITY}:
            raise ValueError("Anthropic route facade requires only the provisioned agent.control capability")
        if host not in _LOCAL_HOSTS:
            raise ValueError("Anthropic route facade host must be a loopback address")
        if not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("Anthropic route facade port must be between 0 and 65535")
        if len(capability_token) < 32:
            raise ValueError("Anthropic route facade capability token is too short")
        self.config = config
        self.host = host
        self.port = port
        self._capability_token = capability_token
        self.governance = ExternalAgentGovernance(config)
        self._server: Optional[asyncio.AbstractServer] = None
        self._stop_event = asyncio.Event()

    @property
    def started(self) -> bool:
        return self._server is not None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None or not self._server.sockets:
            return self.host, self.port
        address = self._server.sockets[0].getsockname()
        return str(address[0]), int(address[1])

    async def start(self) -> None:
        if self._server is None:
            self._stop_event.clear()
            self._server = await asyncio.start_server(self._handle_connection, host=self.host, port=self.port, limit=_MAX_HEADER_BYTES)

    async def run_forever(self) -> None:
        await self.start()
        await self._stop_event.wait()

    async def stop(self) -> None:
        self._stop_event.set()
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()

    def _authorized(self, headers: Mapping[str, str]) -> bool:
        supplied: list[str] = []
        authorization = headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            supplied.append(authorization[7:].strip())
        if headers.get("x-api-key", "").strip():
            supplied.append(headers["x-api-key"].strip())
        return bool(supplied) and all(hmac.compare_digest(value, self._capability_token) for value in supplied)

    async def handle_messages(self, payload: Any) -> AnthropicFacadeResponse:
        try:
            request = translate_anthropic_request(payload)
            result = await self.governance.governed_model_call_async(
                messages=request.messages,
                memory_thread_id=str(uuid4()),
                provider="anthropic",
                model=request.model,
                max_output_tokens=request.max_output_tokens,
                tool_definitions=request.tools or None,
                tool_choice=request.tool_choice,
                stop_sequences=request.stop_sequences or None,
                sampling=request.sampling or None,
                user=request.user,
                stream=False,
            )
            return AnthropicFacadeResponse(200, translate_route_response(result, requested_model=request.model))
        except AnthropicFacadeError as exc:
            return self._error(exc.status, exc.error_type, str(exc))
        except PolicyViolationError:
            return self._error(403, "permission_error", "Model request denied by policy")
        except PolicyTransportError:
            return self._error(503, "api_error", "Atellagent route service is unavailable")
        except Exception:
            return self._error(502, "api_error", "Atellagent route invocation failed")

    @staticmethod
    def _error(status: int, error_type: str, message: str) -> AnthropicFacadeResponse:
        return AnthropicFacadeResponse(status, {"type": "error", "error": {"type": error_type, "message": message}})

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, path, headers, payload = await self._read_request(reader)
            if method != "POST" or path != "/v1/messages":
                response = self._error(404, "not_found_error", "Route not found")
            elif not self._authorized(headers):
                response = self._error(401, "authentication_error", "Invalid local capability credential")
            else:
                response = await self.handle_messages(payload)
        except AnthropicFacadeError as exc:
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

    @staticmethod
    async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str], Any]:
        line = await reader.readline()
        if not line or len(line) > _MAX_HEADER_BYTES:
            raise AnthropicFacadeError("Invalid HTTP request")
        try:
            method, target, version = line.decode("ascii").rstrip("\r\n").split(" ")
        except ValueError as exc:
            raise AnthropicFacadeError("Invalid HTTP request") from exc
        if version != "HTTP/1.1" or "?" in target or "#" in target:
            raise AnthropicFacadeError("Invalid HTTP request")
        headers: dict[str, str] = {}
        total = len(line)
        while True:
            header = await reader.readline()
            total += len(header)
            if total > _MAX_HEADER_BYTES:
                raise AnthropicFacadeError("HTTP headers are too large", status=413)
            if header in {b"\r\n", b"\n"}:
                break
            if not header:
                raise AnthropicFacadeError("Invalid HTTP request")
            try:
                name, value = header.decode("ascii").rstrip("\r\n").split(":", 1)
            except ValueError as exc:
                raise AnthropicFacadeError("Invalid HTTP request") from exc
            name = name.strip().lower()
            if not name or name in headers:
                raise AnthropicFacadeError("Invalid HTTP request")
            headers[name] = value.strip()
        if headers.get("transfer-encoding"):
            raise AnthropicFacadeError("Chunked requests are not supported")
        try:
            size = int(headers.get("content-length", ""))
        except ValueError as exc:
            raise AnthropicFacadeError("Content-Length is required") from exc
        if size < 1 or size > _MAX_BODY_BYTES:
            raise AnthropicFacadeError("Request body is too large", status=413)
        try:
            payload = json.loads(await reader.readexactly(size))
        except (asyncio.IncompleteReadError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AnthropicFacadeError("Request body must be JSON") from exc
        return method, target, headers, payload


def _listen_address(value: str) -> tuple[str, int]:
    host, separator, port = str(value or "").rpartition(":")
    if not separator or not host:
        raise ValueError("--listen must use HOST:PORT")
    try:
        return host, int(port)
    except ValueError as exc:
        raise ValueError("--listen port must be an integer") from exc


async def _run_cli(args: argparse.Namespace) -> None:
    host, port = _listen_address(args.listen)
    runtime = AnthropicMessagesFacadeRuntime(
        load_service_account_config_from_yaml(args.config),
        capability_token=load_route_facade_capability_token(args.token_file),
        host=host,
        port=port,
    )
    await runtime.start()
    address = runtime.address
    print(f"Anthropic Messages route facade listening on http://{address[0]}:{address[1]}")
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run an Atellagent loopback Anthropic Messages route facade.")
    parser.add_argument("config", help="Path to the enrolled connected agent YAML")
    parser.add_argument("--token-file", required=True, help="Absolute owner-only local capability-token file")
    parser.add_argument("--listen", default="127.0.0.1:8787", help="Loopback HOST:PORT")
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run_cli(args))
    except KeyboardInterrupt:
        return
    except ValueError as exc:
        parser.error(str(exc))


__all__ = [
    "AnthropicFacadeError", "AnthropicFacadeResponse", "AnthropicMessagesFacadeRuntime",
    "load_route_facade_capability_token", "main", "translate_anthropic_request", "translate_route_response",
]
