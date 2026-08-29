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
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError


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

    def test_tool_only_provider_loops_are_not_public(self) -> None:
        for bridge in (
            openai_tool_bridge(ingress=_ingress("openai")[1]),
            google_tool_bridge(ingress=_ingress("google")[1]),
            anthropic_tool_bridge(ingress=_ingress("anthropic")[1]),
        ):
            self.assertFalse(hasattr(bridge, "run_tool_loop"))
            self.assertFalse(hasattr(bridge, "run_with_client"))

    def test_denied_tool_and_pep_error_propagate_without_native_result(self) -> None:
        class DenyingClient(_Client):
            async def call_mcp_tool_async(self, *_args, **_kwargs):
                raise PolicyViolationError("tool denied", "tool.denied", {"correlation_id": "c-1"})

        ingress = GovernedToolIngress(
            client=DenyingClient(),
            provider="openai",
            descriptors=_ingress("openai")[1].descriptors,
        )
        with self.assertRaisesRegex(PolicyViolationError, "tool denied"):
            asyncio.run(
                openai_tool_bridge(ingress=ingress).execute_function_call(
                    {
                        "type": "function_call",
                        "name": "read_file",
                        "call_id": "openai-call-1",
                        "arguments": '{"path":"/safe/path"}',
                    }
                )
            )

        class FailingPepClient(_Client):
            async def call_mcp_tool_async(self, *_args, **_kwargs):
                raise PolicyTransportError("tool PEP unavailable")

        failing_ingress = GovernedToolIngress(
            client=FailingPepClient(),
            provider="openai",
            descriptors=_ingress("openai")[1].descriptors,
        )
        with self.assertRaisesRegex(PolicyTransportError, "tool PEP unavailable"):
            asyncio.run(
                openai_tool_bridge(ingress=failing_ingress).execute_function_call(
                    {
                        "type": "function_call",
                        "name": "read_file",
                        "call_id": "openai-call-2",
                        "arguments": '{"path":"/safe/path"}',
                    }
                )
            )


if __name__ == "__main__":
    unittest.main()
