# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Credential-free contracts for provider-native governed tool bridges."""

from __future__ import annotations

import asyncio
import unittest

from atellagent_client.integrations.providers.anthropic import tool_bridge as anthropic_tool_bridge
from atellagent_client.integrations.providers.google import tool_bridge as google_tool_bridge
from atellagent_client.integrations.providers.governed_tools import (
    GovernedToolDescriptor,
    GovernedToolIngress,
)
from atellagent_client.integrations.providers.openai import tool_bridge as openai_tool_bridge


class _Client:
    def __init__(self) -> None:
        self.calls = []

    async def call_mcp_tool_async(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "governed result"


def _ingress(provider: str) -> tuple[_Client, GovernedToolIngress]:
    client = _Client()
    ingress = GovernedToolIngress(
        client=client,
        provider=provider,
        descriptors=[
            GovernedToolDescriptor(
                name="read_file",
                description="Read one governed file.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                target_binding="target-binding",
                target_tool_name="platform_read_file",
            )
        ],
    )
    return client, ingress


class ProviderIntegrationTests(unittest.TestCase):
    def test_openai_native_function_call_routes_to_governed_ingress(self) -> None:
        client, ingress = _ingress("openai")
        bridge = openai_tool_bridge(ingress=ingress)

        self.assertEqual(bridge.tool_definitions()[0]["type"], "function")
        result = asyncio.run(
            bridge.execute_function_call(
                {
                    "type": "function_call",
                    "name": "read_file",
                    "call_id": "openai-call-1",
                    "arguments": '{"path":"/safe/path"}',
                }
            )
        )

        self.assertEqual(result, {
            "type": "function_call_output",
            "call_id": "openai-call-1",
            "output": "governed result",
        })
        self.assertEqual(client.calls[0][1]["tool_call_id"], "openai-call-1")

    def test_openai_tool_loop_returns_only_governed_result_to_follow_up_turn(self) -> None:
        client, ingress = _ingress("openai")
        bridge = openai_tool_bridge(ingress=ingress)
        requests = []

        async def create_response(**kwargs):
            requests.append(kwargs)
            return (
                {"id": "response-1", "output": [{"type": "function_call", "name": "read_file", "call_id": "openai-call-1", "arguments": '{"path":"/safe/path"}'}]}
                if len(requests) == 1
                else {"id": "response-2", "output": [{"type": "message", "content": "done"}]}
            )

        response = asyncio.run(
            bridge.run_tool_loop(
                create_response=create_response,
                request={"model": "test-model", "input": "read the file"},
            )
        )

        self.assertEqual(response["id"], "response-2")
        self.assertEqual(client.calls[0][1]["tool_call_id"], "openai-call-1")
        self.assertEqual(requests[1]["input"], [{"type": "function_call_output", "call_id": "openai-call-1", "output": "governed result"}])

    def test_openai_sdk_client_entry_point_uses_responses_create(self) -> None:
        client, ingress = _ingress("openai")
        bridge = openai_tool_bridge(ingress=ingress)

        class _Responses:
            async def create(self, **_kwargs):
                return {"id": "response-1", "output": []}

        class _OpenAIClient:
            responses = _Responses()

        response = asyncio.run(
            bridge.run_with_client(
                client=_OpenAIClient(), request={"model": "test-model", "input": "hello"}
            )
        )
        self.assertEqual(response["id"], "response-1")

    def test_google_native_function_call_routes_to_governed_ingress(self) -> None:
        client, ingress = _ingress("google")
        bridge = google_tool_bridge(ingress=ingress)

        self.assertEqual(bridge.function_declarations()[0]["name"], "read_file")
        result = asyncio.run(
            bridge.execute_function_call(
                {"name": "read_file", "id": "google-call-1", "args": {"path": "/safe/path"}}
            )
        )

        self.assertEqual(result["function_response"]["name"], "read_file")
        self.assertEqual(result["function_response"]["response"], {"result": "governed result"})
        self.assertEqual(client.calls[0][1]["tool_call_id"], "google-call-1")

    def test_google_tool_loop_returns_native_function_response_after_governance(self) -> None:
        client, ingress = _ingress("google")
        bridge = google_tool_bridge(ingress=ingress)
        requests = []

        async def generate_content(**kwargs):
            requests.append(kwargs)
            return (
                {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "read_file", "id": "google-call-1", "args": {"path": "/safe/path"}}}]}}]}
                if len(requests) == 1
                else {"candidates": [{"content": {"role": "model", "parts": [{"text": "done"}]}}]}
            )

        response = asyncio.run(
            bridge.run_tool_loop(
                generate_content=generate_content,
                request={"model": "test-model", "contents": [{"role": "user", "parts": [{"text": "read the file"}]}]},
            )
        )

        self.assertEqual(response["candidates"][0]["content"]["parts"][0]["text"], "done")
        self.assertEqual(client.calls[0][1]["tool_call_id"], "google-call-1")
        self.assertEqual(requests[1]["contents"][-1]["parts"][0]["function_response"]["id"], "google-call-1")

    def test_google_sdk_client_entry_point_uses_models_generate_content(self) -> None:
        _client, ingress = _ingress("google")
        bridge = google_tool_bridge(ingress=ingress)

        class _Models:
            async def generate_content(self, **_kwargs):
                return {"candidates": [{"content": {"role": "model", "parts": [{"text": "done"}]}}]}

        class _GoogleClient:
            models = _Models()

        response = asyncio.run(
            bridge.run_with_client(
                client=_GoogleClient(),
                request={"model": "test-model", "contents": [{"role": "user", "parts": [{"text": "hello"}]}]},
            )
        )
        self.assertEqual(response["candidates"][0]["content"]["parts"][0]["text"], "done")

    def test_anthropic_native_tool_use_routes_to_governed_ingress(self) -> None:
        client, ingress = _ingress("anthropic")
        bridge = anthropic_tool_bridge(ingress=ingress)

        self.assertEqual(bridge.tool_definitions()[0]["name"], "read_file")
        result = asyncio.run(
            bridge.execute_tool_use(
                {"type": "tool_use", "name": "read_file", "id": "toolu-1", "input": {"path": "/safe/path"}}
            )
        )

        self.assertEqual(result, {"type": "tool_result", "tool_use_id": "toolu-1", "content": "governed result"})
        self.assertEqual(client.calls[0][1]["tool_call_id"], "toolu-1")

    def test_anthropic_tool_loop_returns_tool_result_after_governance(self) -> None:
        client, ingress = _ingress("anthropic")
        bridge = anthropic_tool_bridge(ingress=ingress)
        requests = []

        async def create_message(**kwargs):
            requests.append(kwargs)
            return (
                {"content": [{"type": "tool_use", "name": "read_file", "id": "toolu-1", "input": {"path": "/safe/path"}}]}
                if len(requests) == 1
                else {"content": [{"type": "text", "text": "done"}]}
            )

        response = asyncio.run(
            bridge.run_tool_loop(
                create_message=create_message,
                request={"model": "test-model", "max_tokens": 100, "messages": [{"role": "user", "content": "read the file"}]},
            )
        )

        self.assertEqual(response["content"][0]["text"], "done")
        self.assertEqual(client.calls[0][1]["tool_call_id"], "toolu-1")
        self.assertEqual(requests[1]["messages"][-1]["content"], [{"type": "tool_result", "tool_use_id": "toolu-1", "content": "governed result"}])

    def test_anthropic_sdk_client_entry_point_uses_messages_create(self) -> None:
        _client, ingress = _ingress("anthropic")
        bridge = anthropic_tool_bridge(ingress=ingress)

        class _Messages:
            async def create(self, **_kwargs):
                return {"content": [{"type": "text", "text": "done"}]}

        class _AnthropicClient:
            messages = _Messages()

        response = asyncio.run(
            bridge.run_with_client(
                client=_AnthropicClient(),
                request={"model": "test-model", "max_tokens": 10, "messages": [{"role": "user", "content": "hello"}]},
            )
        )
        self.assertEqual(response["content"][0]["text"], "done")


if __name__ == "__main__":
    unittest.main()
