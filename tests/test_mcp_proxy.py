# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Observable compatibility checks for the public MCP proxy commands."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
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
from atellagent_client.proxy.cli import _LocalMCPToolGateway, _run_local_mcp_bridge
from atellagent_client.integrations.agents.hook_control_protocol import HookControlError
from atellagent_client.sdk.client_modules.mcp_tools import _mcp_tool_result
from atellagent_client.proxy.tool import SESSION_MCP_PROTOCOL_VERSIONS, MCPToolProxyError


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


class MCPAgentProxyTests(unittest.IsolatedAsyncioTestCase):
    def test_local_bridge_uses_a_bounded_mcp_control_timeout(self) -> None:
        with patch("atellagent_client.proxy.cli.HookControlClient") as control_client:
            _LocalMCPToolGateway(control_socket="/private/tmp/test.sock")

        control_client.assert_called_once_with(
            "/private/tmp/test.sock", timeout_seconds=30.0
        )

    async def test_local_bridge_returns_bounded_error_for_local_control_failure(self) -> None:
        with patch(
            "atellagent_client.proxy.cli.load_local_mcp_bridge_config",
            return_value=type("Config", (), {"control_socket": "/private/tmp/test.sock"})(),
        ), patch(
            "atellagent_client.proxy.cli.HookControlClient.call",
            new=AsyncMock(side_effect=HookControlError("control_socket_access_denied")),
        ), patch("sys.stdin", [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26"},
                }
            ) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n",
        ]), patch("builtins.print") as printed:
            await _run_local_mcp_bridge("bridge.yaml")

        rendered = "\n".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("Atellagent MCP control could not complete the request", rendered)
        self.assertNotIn("control_socket_access_denied", rendered)
        response = json.loads(printed.call_args_list[-1].args[0])
        self.assertEqual(response["id"], 2)
        self.assertEqual(response["error"]["code"], -32000)

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

    def test_windows_local_bridge_config_accepts_only_the_local_named_pipe(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge.windows.yaml"
            path.write_text(
                "control_socket: '\\\\.\\pipe\\atellagent-hook-control'\n",
                encoding="utf-8",
            )
            with patch(
                "atellagent_client.proxy.bridge_config._is_windows", return_value=True
            ):
                config = load_local_mcp_bridge_config(str(path))
        self.assertEqual(config.control_socket, r"\\.\pipe\atellagent-hook-control")
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

    async def test_tool_failure_does_not_reflect_transport_detail_to_mcp_peer(self) -> None:
        gateway = _Gateway()
        gateway.invoke_tool = AsyncMock(side_effect=RuntimeError("private transport detail"))
        proxy = MCPAgentProxy(gateway=gateway)
        initialized = await proxy.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )
        response = await proxy.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {}},
            },
            headers={"Mcp-Session-Id": initialized.session_id or ""},
        )
        document = response.document or {}
        self.assertEqual(document["error"]["code"], -32000)
        self.assertEqual(
            document["error"]["message"],
            "Atellagent MCP control could not complete the request",
        )
        self.assertNotIn("private transport detail", json.dumps(document))

    async def test_session_initialization_binds_one_session_and_calls_gateway(self) -> None:
        gateway = _Gateway()
        proxy = MCPAgentProxy(gateway=gateway)
        initialized = await proxy.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {}}}
        )
        self.assertEqual(initialized.protocol, MCPPeerProtocol.STATEFUL)
        self.assertTrue(initialized.session_id)
        response = await proxy.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "echo", "arguments": {"text": "hello"}}},
            headers={"Mcp-Session-Id": initialized.session_id or ""},
        )
        self.assertEqual(response.document["result"]["content"][0]["text"], "ok")
        self.assertEqual(gateway.calls, [("echo", {"text": "hello"}, 2)])

    async def test_each_supported_session_revision_is_bound_to_its_own_session(self) -> None:
        for revision in SESSION_MCP_PROTOCOL_VERSIONS:
            with self.subTest(revision=revision):
                proxy = MCPAgentProxy(gateway=_Gateway())
                response = await proxy.handle(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": revision, "capabilities": {}, "clientInfo": {}}}
                )
                self.assertEqual(response.protocol, MCPPeerProtocol.STATEFUL)
                self.assertTrue(response.session_id)

    async def test_unknown_initialize_refuses_without_protocol_downgrade(self):
        gateway = _Gateway()
        proxy = MCPAgentProxy(gateway=gateway)
        with self.assertRaises(MCPAgentProxyError):
            await proxy.handle({"jsonrpc":"2.0", "id":1, "method":"initialize",
                "params":{"protocolVersion":"2099-01-01"}})
        self.assertEqual(gateway.calls, [])

    async def test_stdio_unknown_revision_does_not_create_a_session(self):
        gateway = _Gateway()
        proxy = MCPAgentProxy(gateway=gateway)
        reply = json.loads(await proxy.handle_json_line(json.dumps({"jsonrpc":"2.0", "id":1,
            "method":"initialize", "params":{"protocolVersion":"2099-01-01"}})))
        self.assertIn("error", reply)
        self.assertEqual(gateway.calls, [])

    async def test_initialize_rejects_malformed_revisions_before_catalog(self) -> None:
        gateway = _Gateway()
        gateway.list_tools = AsyncMock()
        for revision in (None, 123, "", "2025-99-99", "20251125", " 2025-11-25"):
            with self.subTest(revision=revision):
                proxy = MCPAgentProxy(gateway=gateway)
                result = json.loads(await proxy.handle_json_line(json.dumps({"jsonrpc": "2.0", "id": 1,
                    "method": "initialize", "params": {"protocolVersion": revision}})))
                self.assertEqual(result["error"]["code"], -32600)
        gateway.list_tools.assert_not_awaited()

    async def test_ambiguous_session_negotiation_fails_closed(self) -> None:
        proxy = MCPAgentProxy(gateway=_Gateway())
        with self.assertRaisesRegex(MCPAgentProxyError, "disagree"):
            await proxy.handle(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {}}},
                headers={"MCP-Protocol-Version": "2024-11-05"},
            )

    async def test_modern_discovery_is_stateless_and_sessions_are_not_inferred(self) -> None:
        proxy = MCPAgentProxy(gateway=_Gateway())
        discovery = await proxy.handle(
            {"jsonrpc": "2.0", "id": "d", "method": "server/discover", "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}}
        )
        self.assertEqual(discovery.protocol, MCPPeerProtocol.MODERN)
        self.assertEqual(discovery.document["result"]["supportedVersions"], ["2026-07-28"])
        with self.assertRaises(MCPAgentProxyError):
            await proxy.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})

    async def test_stdio_keeps_negotiation_for_the_connection_lifetime(self) -> None:
        proxy = MCPAgentProxy(gateway=_Gateway())
        with patch("atellagent_client.proxy.agent.monotonic", return_value=100):
            initialized = await proxy.handle_json_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {}}}))
        self.assertIn("protocolVersion", initialized or "")
        with patch("atellagent_client.proxy.agent.monotonic", return_value=100000):
            listed = await proxy.handle_json_line(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
            called = await proxy.handle_json_line(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "echo", "arguments": {"text": "hello"}}}))
        self.assertIn("echo", listed or "")
        self.assertEqual(json.loads(called)["result"]["content"][0]["text"], "ok")

    async def test_stdio_request_errors_keep_the_call_id(self) -> None:
        proxy = MCPAgentProxy(gateway=_Gateway())
        rejected = await proxy.handle_json_line(json.dumps({"jsonrpc": "2.0", "id": 77, "method": "tools/call", "params": {"name": "echo"}}))
        self.assertEqual(json.loads(rejected)["id"], 77)
        self.assertIn("error", json.loads(rejected))
        invalid = await proxy.handle_json_line("not-json")
        self.assertIsNone(json.loads(invalid)["id"])
        self.assertEqual(json.loads(invalid)["error"]["code"], -32700)

    async def test_http_sessions_still_expire(self) -> None:
        proxy = MCPAgentProxy(gateway=_Gateway())
        with patch("atellagent_client.proxy.agent.monotonic", return_value=100):
            response = await proxy.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
        with patch("atellagent_client.proxy.agent.monotonic", return_value=100000), self.assertRaisesRegex(MCPAgentProxyError, "expired"):
            await proxy.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers={"Mcp-Session-Id": response.session_id})
