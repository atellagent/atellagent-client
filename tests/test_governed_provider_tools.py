# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Contract tests for the provider-native governed function-tool bridge."""

from __future__ import annotations

import asyncio
import hashlib
import json
import unittest

from atellagent_client.integrations.providers.governed_tools import (
    GovernedToolDescriptor,
    GovernedToolIngress,
)
from atellagent_client.sdk.operations_modules.mcp import mcp_communicate_sync


class _Client:
    def __init__(self) -> None:
        self.calls = []

    def call_mcp_tool(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "sync result"

    async def call_mcp_tool_async(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "async result"


class _GatewayResponse:
    status_code = 200
    content = b"{}"

    def json(self):
        return {"success": True}


class _GatewayClient:
    def __init__(self) -> None:
        self.payload = None

    def post(self, _url, *, json, headers):
        self.payload = {"json": json, "headers": headers}
        return _GatewayResponse()


def _descriptor() -> GovernedToolDescriptor:
    return GovernedToolDescriptor(
        name="lookup_customer",
        description="Look up one customer by identifier.",
        input_schema={
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
            "additionalProperties": False,
        },
        target_binding="target-binding",
        target_tool_name="internal_lookup_customer",
        tool_id="catalog-tool-id",
        policy_metadata={"classification": "customer_data"},
    )


class GovernedProviderToolTests(unittest.TestCase):
    def test_public_provider_definitions_exclude_transport_and_credentials(self) -> None:
        descriptor = _descriptor()

        definitions = [
            descriptor.to_openai_tool(),
            descriptor.to_anthropic_tool(),
            descriptor.to_google_tool(),
        ]
        for definition in definitions:
            serialized = str(definition)
            self.assertNotIn("mcp-service-account-id", serialized)
            self.assertNotIn("internal_lookup_customer", serialized)
            self.assertNotIn("catalog-tool-id", serialized)
            self.assertNotIn("http", serialized.lower())
            self.assertNotIn("credential", serialized.lower())

    def test_unknown_provider_tool_cannot_execute(self) -> None:
        client = _Client()
        ingress = GovernedToolIngress(
            client=client,
            descriptors=[_descriptor()],
            provider="openai",
        )

        with self.assertRaisesRegex(ValueError, "unknown governed provider tool"):
            ingress.invoke_sync(
                provider_tool_name="not_registered",
                arguments={},
                provider_tool_call_id="call-1",
            )
        self.assertEqual(client.calls, [])

    def test_sync_tool_call_uses_gateway_ingress_with_provider_call_identity(self) -> None:
        client = _Client()
        ingress = GovernedToolIngress(
            client=client,
            descriptors=[_descriptor()],
            provider="anthropic",
            source_agent="agent-service-account",
            workflow_context={"workflow_execution_id": "execution-1"},
        )

        result = ingress.invoke_sync(
            provider_tool_name="lookup_customer",
            arguments={"customer_id": "customer-1"},
            provider_tool_call_id="toolu_1",
        )

        self.assertEqual(result, "sync result")
        args, kwargs = client.calls[0]
        self.assertEqual(args, ("target-binding", "internal_lookup_customer", {"customer_id": "customer-1"}))
        self.assertEqual(kwargs["tool_call_id"], "toolu_1")
        self.assertEqual(kwargs["source_agent"], "agent-service-account")
        self.assertEqual(kwargs["workflow_context"], {"workflow_execution_id": "execution-1"})
        self.assertEqual(
            kwargs["action_context"]["provider_tool"],
            {
                "schema_version": "atellagent.provider-tool-bridge.v2",
                "provider": "anthropic",
                "provider_tool_name": "lookup_customer",
                "provider_tool_call_id": "toolu_1",
                "tool_id": "catalog-tool-id",
                "target_input_schema_sha256": hashlib.sha256(
                    json.dumps(
                        _descriptor().input_schema,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
            },
        )

    def test_async_tool_call_uses_gateway_ingress(self) -> None:
        client = _Client()
        ingress = GovernedToolIngress(
            client=client,
            descriptors=[_descriptor()],
            provider="google",
        )

        result = asyncio.run(
            ingress.invoke_async(
                provider_tool_name="lookup_customer",
                arguments={"customer_id": "customer-1"},
                provider_tool_call_id="google-call-1",
            )
        )

        self.assertEqual(result, "async result")
        self.assertEqual(client.calls[0][1]["tool_call_id"], "google-call-1")

    def test_action_context_is_submitted_without_overriding_canonical_call_facts(self) -> None:
        gateway = _GatewayClient()
        mcp_communicate_sync(
            base_url="https://gateway.example.test",
            api_version="v1",
            client=gateway,
            headers={"Authorization": "Bearer test"},
            source_agent="source-agent",
            target_agent="mcp-service-account",
            tool_name="internal_lookup_customer",
            arguments={"customer_id": "customer-1"},
            tool_call_id="provider-call-1",
            action_context={
                "tool_name": "attempted-override",
                "tool_call_id": "attempted-override",
                "provider_tool": {"provider": "openai"},
            },
        )

        context = gateway.payload["json"]["context"]
        self.assertEqual(context["tool_name"], "internal_lookup_customer")
        self.assertEqual(context["tool_call_id"], "provider-call-1")
        self.assertEqual(context["provider_tool"], {"provider": "openai"})


if __name__ == "__main__":
    unittest.main()
