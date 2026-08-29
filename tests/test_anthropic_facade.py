# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public wire-contract tests for the non-streaming Anthropic route facade."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from anthropic import AsyncAnthropic

from atellagent_client.integrations.agents.anthropic_facade import (
    AnthropicMessagesFacadeRuntime,
    load_route_facade_capability_token,
    translate_anthropic_request,
)
from atellagent_client.sdk.config_models import SDKDeploymentConfig, ServiceAccountConfig
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError


_TOKEN = "local-capability-token-for-test-only-0001"


def _config() -> ServiceAccountConfig:
    return ServiceAccountConfig(
        client_id="client-id",
        gateway_url="https://mtls.gateway.example",
        oauth_token_url="https://mtls.auth.example/token",
        oauth_jwks_url="https://mtls.auth.example/jwks",
        service_account_id=str(uuid4()),
        integration_id=str(uuid4()),
        tenant_id=str(uuid4()),
        capabilities=["agent.control"],
        cert_path="/tmp/client.crt",
        key_path="/tmp/client.key",
        integration_type="agent",
        identity_mode="boundary_identity_only",
        deployment=SDKDeploymentConfig(),
    )


class _Governance:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.failure: Exception | None = None

    async def governed_model_call_async(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return {
            "model": kwargs["model"],
            "output_text": "route answer",
            "finish_reason": "stop",
            "response_id": "route-response-id",
            "usage": {"input_tokens": 12, "output_tokens": 8},
            "tool_requests": [
                {"id": "toolu-1", "name": "lookup", "arguments": {"record": "42"}}
            ],
        }


class AnthropicFacadeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.governance = _Governance()
        self.constructor = patch(
            "atellagent_client.integrations.agents.anthropic_facade.ExternalAgentGovernance",
            return_value=self.governance,
        )
        self.constructor.start()
        self.addCleanup(self.constructor.stop)
        self.runtime = AnthropicMessagesFacadeRuntime(
            _config(), capability_token=_TOKEN, host="127.0.0.1", port=0
        )
        await self.runtime.start()
        self.addAsyncCleanup(self.runtime.stop)

    async def _request(self, payload: dict, *, token: str = _TOKEN, path: str = "/v1/messages") -> tuple[int, dict]:
        host, port = self.runtime.address
        reader, writer = await asyncio.open_connection(host, port)
        encoded = json.dumps(payload).encode("utf-8")
        writer.write(
            (
                f"POST {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Authorization: Bearer {token}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(encoded)}\r\n\r\n"
            ).encode("ascii")
            + encoded
        )
        await writer.drain()
        raw = await reader.read()
        writer.close()
        await writer.wait_closed()
        head, body = raw.split(b"\r\n\r\n", 1)
        return int(head.splitlines()[0].split()[1]), json.loads(body)

    async def test_non_streaming_messages_tools_and_usage_translate_to_one_route_call(self) -> None:
        status, payload = await self._request(
            {
                "model": "claude-test",
                "max_tokens": 256,
                "system": [{"type": "text", "text": "Be concise."}],
                "messages": [
                    {"role": "user", "content": "Find record 42."},
                    {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": "toolu-old", "name": "lookup", "input": {"record": "41"}}],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "toolu-old", "content": "not found"}],
                    },
                ],
                "tools": [{"name": "lookup", "description": "Lookup a record", "input_schema": {"type": "object"}}],
                "tool_choice": {"type": "auto"},
                "temperature": 0.2,
                "top_p": 0.8,
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["type"], "message")
        self.assertEqual(payload["stop_reason"], "tool_use")
        self.assertEqual(payload["usage"], {"input_tokens": 12, "output_tokens": 8})
        self.assertEqual(payload["content"][-1]["input"], {"record": "42"})
        self.assertEqual(len(self.governance.calls), 1)
        routed = self.governance.calls[0]
        self.assertEqual(routed["provider"], "anthropic")
        self.assertFalse(routed["stream"])
        self.assertEqual(routed["tool_choice"], {"type": "auto"})
        self.assertEqual(routed["sampling"], {"temperature": 0.2, "top_p": 0.8})
        self.assertEqual(routed["messages"][0], {"role": "system", "content": "Be concise."})
        self.assertEqual(routed["messages"][-1]["tool_call_id"], "toolu-old")

    async def test_current_anthropic_sdk_can_parse_the_facade_response(self) -> None:
        host, port = self.runtime.address
        client = AsyncAnthropic(api_key=_TOKEN, base_url=f"http://{host}:{port}")
        try:
            response = await client.messages.create(
                model="claude-test",
                max_tokens=8,
                messages=[{"role": "user", "content": "hello"}],
            )
        finally:
            await client.close()
        self.assertEqual(response.type, "message")
        self.assertEqual(response.stop_reason, "tool_use")
        self.assertEqual(response.usage.input_tokens, 12)
        self.assertEqual(response.content[-1].type, "tool_use")

    async def test_invalid_local_credential_and_streaming_never_invoke_the_route(self) -> None:
        request = {"model": "claude-test", "max_tokens": 8, "messages": [{"role": "user", "content": "hello"}]}
        status, payload = await self._request(request, token="not-the-local-capability")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["type"], "authentication_error")
        self.assertEqual(self.governance.calls, [])
        status, payload = await self._request({**request, "stream": True})
        self.assertEqual(status, 400)
        self.assertIn("streaming is not supported", payload["error"]["message"])
        self.assertEqual(self.governance.calls, [])

    async def test_route_denial_and_transport_failure_are_safe_anthropic_errors(self) -> None:
        request = {"model": "claude-test", "max_tokens": 8, "messages": [{"role": "user", "content": "hello"}]}
        self.governance.failure = PolicyViolationError("private reason", "policy.denied")
        status, payload = await self._request(request)
        self.assertEqual((status, payload["error"]["type"]), (403, "permission_error"))
        self.assertEqual(payload["error"]["message"], "Model request denied by policy")
        self.governance.failure = PolicyTransportError("private topology")
        status, payload = await self._request(request)
        self.assertEqual((status, payload["error"]["type"]), (503, "api_error"))
        self.assertNotIn("topology", payload["error"]["message"])

    def test_translation_rejects_streaming_and_unsupported_provider_features(self) -> None:
        with self.assertRaisesRegex(ValueError, "streaming"):
            translate_anthropic_request({"model": "claude-test", "max_tokens": 1, "stream": True, "messages": [{"role": "user", "content": "hello"}]})
        with self.assertRaisesRegex(ValueError, "unsupported request fields"):
            translate_anthropic_request({"model": "claude-test", "max_tokens": 1, "messages": [{"role": "user", "content": "hello"}], "thinking": {"type": "enabled"}})

    def test_runtime_requires_the_standard_enrolled_loopback_boundary(self) -> None:
        config = _config()
        config.capabilities = ["agent.control", "agent.process"]
        with self.assertRaisesRegex(ValueError, "only the provisioned"):
            AnthropicMessagesFacadeRuntime(config, capability_token=_TOKEN)
        with self.assertRaisesRegex(ValueError, "loopback"):
            AnthropicMessagesFacadeRuntime(_config(), capability_token=_TOKEN, host="0.0.0.0")

    def test_token_file_must_be_private_and_local(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "facade-token"
            token_file.write_text(_TOKEN, encoding="utf-8")
            token_file.chmod(0o600)
            self.assertEqual(load_route_facade_capability_token(str(token_file)), _TOKEN)
            token_file.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "owner-only"):
                load_route_facade_capability_token(str(token_file))
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()
