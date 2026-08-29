# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Observable compatibility checks for the public MCP proxy commands."""

from __future__ import annotations

import json
import unittest
from typing import Any, Mapping, Sequence

import httpx

from atellagent_client.proxy import (
    MCPAgentProxy,
    MCPPeerProtocol,
    MCPProxyTool,
    MCPToolProxy,
    MCPToolResult,
    MCPToolTarget,
)
from atellagent_client.proxy.agent import MCPAgentProxyError
from atellagent_client.proxy.contracts import _ConfiguredMCPToolGateway
from atellagent_client.proxy.bridge_config import load_local_mcp_bridge_config
from atellagent_client.sdk.client_modules.mcp_tools import _mcp_tool_result
from atellagent_client.proxy.tool import LEGACY_MCP_PROTOCOL_VERSIONS, MCPToolProxyError


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any], str | int | None]] = []

    async def list_tools(self) -> Sequence[MCPProxyTool]:
        return (
            MCPProxyTool(
                name="echo",
                description="Echo text.",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                target_binding="target",
                target_tool_name="echo",
            ),
        )

    async def invoke_tool(self, *, tool_name: str, arguments: Mapping[str, Any], peer_call_id: str | int | None) -> MCPToolResult:
        self.calls.append((tool_name, arguments, peer_call_id))
        return MCPToolResult(content=({"type": "text", "text": "ok"},))


class _BridgeClient:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def call_mcp_tool_result_async(self, *_args, **_kwargs):
        self.kwargs = dict(_kwargs)
        return {
            "content": (
                {"type": "text", "text": "native result"},
                {"type": "resource", "resource": {"uri": "atellagent://record/1"}},
            ),
            "isError": True,
        }


class _ProxyWithTransport(MCPToolProxy):
    def __init__(self, *, target: MCPToolTarget, handler) -> None:
        super().__init__(target=target)
        self._transport = httpx.MockTransport(handler)

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, trust_env=False)


def json_body(request: httpx.Request) -> Mapping[str, Any]:
    value = json.loads(request.content)
    assert isinstance(value, Mapping)
    return value


class MCPAgentProxyTests(unittest.IsolatedAsyncioTestCase):
    def test_local_bridge_config_has_no_client_credential_path(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge.yaml"
            path.write_text(
                "control_socket: /private/tmp/atellagent/control.sock\n",
                encoding="utf-8",
            )
            config = load_local_mcp_bridge_config(str(path))
        self.assertEqual(config.control_socket, "/private/tmp/atellagent/control.sock")
    def test_native_result_extraction_keeps_content_blocks(self) -> None:
        result = _mcp_tool_result(
            {
                "response": {
                    "jsonrpc": "2.0",
                    "id": "call-1",
                    "result": {
                        "content": [{"type": "text", "text": "record"}],
                        "isError": True,
                    },
                }
            }
        )
        self.assertEqual(result["content"], [{"type": "text", "text": "record"}])
        self.assertTrue(result["isError"])

    def test_native_result_extraction_unwraps_gateway_action_envelope(self) -> None:
        result = _mcp_tool_result(
            {
                "response": {
                    "content": "display-ready text",
                    "mcp_result": {
                        "content": [{"type": "text", "text": "native record"}],
                    },
                }
            }
        )
        self.assertEqual(
            result["content"], [{"type": "text", "text": "native record"}]
        )

    async def test_configured_bridge_preserves_native_mcp_content(self) -> None:
        client = _BridgeClient()
        gateway = _ConfiguredMCPToolGateway(
            client=client,
            tools=(
                MCPProxyTool(
                    name="lookup",
                    description="Look up a record.",
                    input_schema={"type": "object", "properties": {}},
                    target_binding="external-resource-id",
                    target_tool_name="lookup",
                ),
            ),
        )
        result = await gateway.invoke_tool(
            tool_name="lookup", arguments={}, peer_call_id="call-1"
        )
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0]["text"], "native result")
        self.assertEqual(result.content[1]["type"], "resource")
        action_key = client.kwargs["action_context"]["action_key"]
        self.assertTrue(action_key.startswith("mcp-bridge-"))
        self.assertNotEqual(action_key, "call-1")

    async def test_legacy_initialization_binds_one_session_and_calls_gateway(self) -> None:
        gateway = _Gateway()
        proxy = MCPAgentProxy(gateway=gateway)
        initialized = await proxy.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {}}}
        )
        self.assertEqual(initialized.protocol, MCPPeerProtocol.LEGACY_STREAMABLE_HTTP)
        self.assertTrue(initialized.session_id)
        response = await proxy.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "echo", "arguments": {"text": "hello"}}},
            headers={"Mcp-Session-Id": initialized.session_id or ""},
        )
        self.assertEqual(response.document["result"]["content"][0]["text"], "ok")
        self.assertEqual(gateway.calls, [("echo", {"text": "hello"}, 2)])

    async def test_each_supported_legacy_revision_is_bound_to_its_own_session(self) -> None:
        for revision in LEGACY_MCP_PROTOCOL_VERSIONS:
            with self.subTest(revision=revision):
                proxy = MCPAgentProxy(gateway=_Gateway())
                response = await proxy.handle(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": revision, "capabilities": {}, "clientInfo": {}}}
                )
                self.assertEqual(response.protocol, MCPPeerProtocol.LEGACY_STREAMABLE_HTTP)
                self.assertTrue(response.session_id)

    async def test_ambiguous_legacy_negotiation_fails_closed(self) -> None:
        proxy = MCPAgentProxy(gateway=_Gateway())
        with self.assertRaisesRegex(MCPAgentProxyError, "disagree"):
            await proxy.handle(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {}}},
                headers={"MCP-Protocol-Version": "2024-11-05"},
            )

    async def test_modern_discovery_is_stateless_and_legacy_is_not_inferred(self) -> None:
        proxy = MCPAgentProxy(gateway=_Gateway())
        discovery = await proxy.handle(
            {"jsonrpc": "2.0", "id": "d", "method": "server/discover", "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}}
        )
        self.assertEqual(discovery.protocol, MCPPeerProtocol.MODERN)
        self.assertEqual(discovery.document["result"]["supportedVersions"], ["2026-07-28"])
        with self.assertRaises(MCPAgentProxyError):
            await proxy.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})

    async def test_stdio_keeps_only_its_bounded_legacy_connection_session(self) -> None:
        proxy = MCPAgentProxy(gateway=_Gateway())
        initialized = await proxy.handle_json_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {}}}))
        self.assertIn("protocolVersion", initialized or "")
        listed = await proxy.handle_json_line(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
        self.assertIn("echo", listed or "")


class MCPToolProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_modern_discovery_and_action_key_are_preserved(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            payload = json_body(request)
            if payload["method"] == "server/discover":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {"resultType": "complete", "supportedVersions": ["2026-07-28"], "capabilities": {}}})
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {"resultType": "complete", "content": [{"type": "text", "text": "ok"}]}})

        proxy = _ProxyWithTransport(target=MCPToolTarget(endpoint_url="https://tools.example/mcp"), handler=handler)
        result = await proxy.call_tool(tool_name="echo", arguments={"text": "hello"}, action_key="issued-action-1")
        self.assertEqual(result["content"][0]["text"], "ok")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1].headers["idempotency-key"], "issued-action-1")
        self.assertEqual(json_body(requests[1])["method"], "tools/call")

    async def test_modern_noncompatibility_status_allows_explicit_legacy_negotiation(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json_body(request)
            if payload["method"] == "server/discover":
                return httpx.Response(404)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": "2025-03-26", "capabilities": {}}}, headers={"mcp-session-id": "peer-session"})

        proxy = _ProxyWithTransport(target=MCPToolTarget(endpoint_url="https://tools.example/mcp"), handler=handler)
        self.assertEqual(await proxy.selected_protocol(), MCPPeerProtocol.LEGACY_STREAMABLE_HTTP)

    async def test_invalid_modern_discovery_does_not_downgrade_to_legacy(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            payload = json_body(request)
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": {"resultType": "complete", "supportedVersions": ["2025-03-26"], "capabilities": {}}},
            )

        proxy = _ProxyWithTransport(target=MCPToolTarget(endpoint_url="https://tools.example/mcp"), handler=handler)
        with self.assertRaisesRegex(MCPToolProxyError, "discovery response is invalid"):
            await proxy.selected_protocol()
        self.assertEqual(len(requests), 1)

    async def test_legacy_sse_resumes_once_without_replaying_the_tool_call(self) -> None:
        requests: list[httpx.Request] = []
        result_id: str | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal result_id
            requests.append(request)
            if request.method == "GET":
                if "last-event-id" in request.headers:
                    return httpx.Response(
                        200,
                        headers={"content-type": "text/event-stream"},
                        content=(
                            "data: "
                            + json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": result_id,
                                    "result": {"content": [{"type": "text", "text": "resumed"}]},
                                }
                            )
                            + "\n\n"
                        ),
                    )
                if len([item for item in requests if item.method == "GET"]) == 1:
                    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="event: endpoint\ndata: /messages\n\n")
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="id: cursor-1\n\n")
            payload = json_body(request)
            if request.url.path == "/mcp" and payload["method"] in {"server/discover", "initialize"}:
                return httpx.Response(404)
            if payload["method"] == "tools/call":
                result_id = payload["id"]
            return httpx.Response(202)

        proxy = _ProxyWithTransport(target=MCPToolTarget(endpoint_url="https://tools.example/mcp"), handler=handler)
        result = await proxy.call_tool(tool_name="echo", arguments={"text": "hello"}, action_key="issued-action-1")
        self.assertEqual(result["content"][0]["text"], "resumed")
        self.assertEqual(len([request for request in requests if request.method == "POST" and json_body(request).get("method") == "tools/call"]), 1)
        resumed = [request for request in requests if request.method == "GET" and "last-event-id" in request.headers]
        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0].headers["last-event-id"], "cursor-1")

    async def test_legacy_sse_without_cursor_fails_without_replaying_the_tool_call(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                if len([item for item in requests if item.method == "GET"]) == 1:
                    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="event: endpoint\ndata: /messages\n\n")
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="")
            payload = json_body(request)
            if request.url.path == "/mcp" and payload["method"] in {"server/discover", "initialize"}:
                return httpx.Response(404)
            return httpx.Response(202)

        proxy = _ProxyWithTransport(target=MCPToolTarget(endpoint_url="https://tools.example/mcp"), handler=handler)
        with self.assertRaisesRegex(Exception, "without a resumable event cursor"):
            await proxy.call_tool(tool_name="echo", arguments={"text": "hello"}, action_key="issued-action-1")
        self.assertEqual(len([request for request in requests if request.method == "POST" and json_body(request).get("method") == "tools/call"]), 1)


if __name__ == "__main__":
    unittest.main()
