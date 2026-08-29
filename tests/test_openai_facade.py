# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public wire-contract tests for the non-streaming Responses facade."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from openai import AsyncOpenAI

from atellagent_client.integrations.agents.openai_facade import (
    OpenAIResponsesFacadeRuntime,
    translate_openai_response_request,
)
from atellagent_client.sdk.config_models import SDKDeploymentConfig, ServiceAccountConfig
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError


_TOKEN = "local-capability-token-for-openai-test-0001"


def _config() -> ServiceAccountConfig:
    return ServiceAccountConfig(
        client_id="client-id", gateway_url="https://mtls.gateway.example",
        oauth_token_url="https://mtls.auth.example/token", oauth_jwks_url="https://mtls.auth.example/jwks",
        service_account_id=str(uuid4()), integration_id=str(uuid4()), tenant_id=str(uuid4()),
        capabilities=["agent.control"], cert_path="/tmp/client.crt", key_path="/tmp/client.key",
        integration_type="agent", identity_mode="boundary_identity_only", deployment=SDKDeploymentConfig(),
    )


class _Governance:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.failure: Exception | None = None

    async def governed_model_call_async(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure:
            raise self.failure
        return {
            "model": kwargs["model"], "output_text": "route answer", "finish_reason": "stop",
            "response_id": "resp_route_test", "usage": {"input_tokens": 12, "output_tokens": 8},
            "tool_requests": [{"id": "call_1", "name": "lookup", "arguments": {"record": "42"}}],
        }


class OpenAIResponsesFacadeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.governance = _Governance()
        constructor = patch("atellagent_client.integrations.agents.anthropic_facade.ExternalAgentGovernance", return_value=self.governance)
        constructor.start()
        self.addCleanup(constructor.stop)
        self.runtime = OpenAIResponsesFacadeRuntime(_config(), capability_token=_TOKEN, host="127.0.0.1", port=0)
        await self.runtime.start()
        self.addAsyncCleanup(self.runtime.stop)

    async def _request(self, payload: dict, *, token: str = _TOKEN) -> tuple[int, dict]:
        host, port = self.runtime.address
        body = json.dumps(payload).encode()
        reader, writer = await asyncio.open_connection(host, port)
        writer.write((f"POST /v1/responses HTTP/1.1\r\nHost: {host}\r\nAuthorization: Bearer {token}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body)
        await writer.drain()
        raw = await reader.read()
        writer.close()
        await writer.wait_closed()
        head, response = raw.split(b"\r\n\r\n", 1)
        return int(head.splitlines()[0].split()[1]), json.loads(response)

    async def test_supported_request_tools_and_usage_translate_to_one_route_call(self) -> None:
        status, payload = await self._request({
            "model": "gpt-test", "input": [{"role": "user", "content": [{"type": "input_text", "text": "Find 42"}]}],
            "instructions": "Be concise.", "max_output_tokens": 64,
            "tools": [{"type": "function", "name": "lookup", "description": "Lookup a record", "parameters": {"type": "object"}}],
            "tool_choice": {"type": "function", "name": "lookup"}, "parallel_tool_calls": False,
            "temperature": 0.2, "top_p": 0.8, "metadata": {"case": "test"}, "user": "user-1",
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["object"], "response")
        self.assertEqual(payload["usage"]["total_tokens"], 20)
        self.assertEqual(payload["output"][-1]["call_id"], "call_1")
        self.assertEqual(len(self.governance.calls), 1)
        routed = self.governance.calls[0]
        self.assertEqual(routed["provider"], "openai")
        self.assertFalse(routed["stream"])
        self.assertEqual(routed["messages"][0], {"role": "system", "content": "Be concise."})
        self.assertEqual(routed["sampling"], {"temperature": 0.2, "top_p": 0.8})
        self.assertEqual(routed["metadata"], {"case": "test"})
        self.assertEqual(routed["tool_definitions"], [{
            "type": "function",
            "function": {"name": "lookup", "description": "Lookup a record", "parameters": {"type": "object"}},
        }])
        self.assertEqual(routed["tool_choice"], {"type": "function", "function": {"name": "lookup"}})

    async def test_current_openai_sdk_parses_the_non_streaming_response(self) -> None:
        host, port = self.runtime.address
        client = AsyncOpenAI(api_key=_TOKEN, base_url=f"http://{host}:{port}/v1")
        try:
            response = await client.responses.create(model="gpt-test", input="hello")
        finally:
            await client.close()
        self.assertEqual(response.object, "response")
        self.assertEqual(response.status, "completed")
        self.assertEqual(response.usage.input_tokens, 12)
        self.assertEqual(response.output[-1].type, "function_call")

    async def test_invalid_credential_and_streaming_never_invoke_the_route(self) -> None:
        request = {"model": "gpt-test", "input": "hello"}
        status, payload = await self._request(request, token="not-the-local-capability")
        self.assertEqual((status, payload["error"]["type"]), (401, "authentication_error"))
        self.assertEqual(self.governance.calls, [])
        status, payload = await self._request({**request, "stream": True})
        self.assertEqual(status, 400)
        self.assertIn("streaming is not supported", payload["error"]["message"])
        self.assertEqual(self.governance.calls, [])

    async def test_policy_and_transport_errors_are_safe(self) -> None:
        request = {"model": "gpt-test", "input": "hello"}
        self.governance.failure = PolicyViolationError("private reason", "policy.denied")
        status, payload = await self._request(request)
        self.assertEqual((status, payload["error"]["type"]), (403, "permission_error"))
        self.assertEqual(payload["error"]["message"], "Model request denied by policy")
        self.governance.failure = PolicyTransportError("private topology")
        status, payload = await self._request(request)
        self.assertEqual((status, payload["error"]["type"]), (503, "server_error"))
        self.assertNotIn("topology", payload["error"]["message"])

    def test_translation_rejects_streaming_storage_and_unsupported_items(self) -> None:
        request = {"model": "gpt-test", "input": "hello"}
        with self.assertRaisesRegex(ValueError, "streaming"):
            translate_openai_response_request({**request, "stream": True})
        with self.assertRaisesRegex(ValueError, "store"):
            translate_openai_response_request({**request, "store": True})
        with self.assertRaisesRegex(ValueError, "unsupported"):
            translate_openai_response_request({**request, "reasoning": {"effort": "high"}})


if __name__ == "__main__":
    unittest.main()
