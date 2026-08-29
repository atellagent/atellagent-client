# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from atellagent_client.connected.mcp_client import (
    LocalMCPClient,
    _require_loopback_url,
    _validate_discovery,
)
from atellagent_client.sdk.config_models import BridgeDeploymentConfig


class LocalMCPClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_the_pinned_reference_server_contract(self) -> None:
        reference_server = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "modern_mcp_reference_server.py"
        )
        client = LocalMCPClient(
            BridgeDeploymentConfig(
                target_transport="stdio",
                target_command=sys.executable,
                target_args=["-u", str(reference_server)],
            )
        )
        try:
            manifest = await client.manifest()
            response = await client.invoke(
                {
                    "jsonrpc": "2.0",
                    "id": "reference-call",
                    "method": "tools/call",
                    "params": {"name": "echo", "arguments": {}},
                },
                "reference-effect-1",
            )
        finally:
            await client.close()

        self.assertEqual(manifest["tools"][0]["name"], "echo")
        self.assertEqual(response["result"]["content"][0]["text"], "ok")

    async def test_binds_effect_key_to_tools_call(self) -> None:
        target = LocalMCPClient(
            BridgeDeploymentConfig(target_transport="stdio", target_command="example-mcp")
        )
        result_value = SimpleNamespace(
            model_dump=Mock(return_value={"content": [{"type": "text", "text": "ok"}]})
        )
        target._client = SimpleNamespace(call_tool=AsyncMock(return_value=result_value))
        response = await target.invoke(
            {
                "jsonrpc": "2.0",
                "id": "request-1",
                "method": "tools/call",
                "params": {"name": "lookup", "arguments": {"id": 7}},
            },
            "effect-1",
        )
        target._client.call_tool.assert_awaited_once_with(
            "lookup", {"id": 7}, meta={"atellagent/idempotencyKey": "effect-1"}
        )
        self.assertEqual(response["id"], "request-1")
        self.assertEqual(response["result"]["content"][0]["text"], "ok")

    async def test_transport_failure_is_not_retried(self) -> None:
        target = LocalMCPClient(
            BridgeDeploymentConfig(target_transport="stdio", target_command="example-mcp")
        )
        client = SimpleNamespace(call_tool=AsyncMock(side_effect=ConnectionError("lost")))
        target._client = client
        target.close = AsyncMock()
        with self.assertRaises(ConnectionError):
            await target.invoke(
                {
                    "jsonrpc": "2.0",
                    "id": "request-1",
                    "method": "tools/call",
                    "params": {"name": "lookup", "arguments": {}},
                },
                "effect-1",
            )
        client.call_tool.assert_awaited_once()
        target.close.assert_awaited_once()

    async def test_manifest_bypasses_discovery_cache(self) -> None:
        target = LocalMCPClient(
            BridgeDeploymentConfig(target_transport="stdio", target_command="example-mcp")
        )
        listed_tool = SimpleNamespace(
            model_dump=Mock(return_value={"name": "lookup", "inputSchema": {}})
        )
        client = SimpleNamespace(
            list_tools=AsyncMock(return_value=SimpleNamespace(tools=[listed_tool]))
        )
        target._client = client
        manifest = await target.manifest()
        client.list_tools.assert_awaited_once_with(cache_mode="bypass")
        self.assertEqual(manifest, {"tools": [{"name": "lookup", "inputSchema": {}}]})

    def test_requires_a_complete_modern_discovery_result(self) -> None:
        _validate_discovery(
            {
                "resultType": "complete",
                "supportedVersions": ["2026-07-28"],
                "cacheScope": "private",
                "ttlMs": 0,
                "capabilities": {},
            }
        )
        with self.assertRaisesRegex(ValueError, "does not support"):
            _validate_discovery(
                {
                    "resultType": "complete",
                    "supportedVersions": ["2025-06-18"],
                    "cacheScope": "private",
                    "ttlMs": 0,
                    "capabilities": {},
                }
            )

    def test_http_target_is_loopback_only(self) -> None:
        self.assertEqual(
            _require_loopback_url("http://127.0.0.1:9000/mcp"),
            "http://127.0.0.1:9000/mcp",
        )
        with self.assertRaisesRegex(ValueError, "loopback"):
            _require_loopback_url("https://mcp.example.com/mcp")

    def test_http_auth_is_loaded_only_from_environment(self) -> None:
        target = LocalMCPClient(
            BridgeDeploymentConfig(
                target_transport="http",
                target_url="http://127.0.0.1:9000/mcp",
                upstream_headers={"X-Static": "reviewed"},
                upstream_auth_header="X-Local-Token",
                upstream_auth_token_env="LOCAL_MCP_TOKEN",
            )
        )
        with patch.dict(os.environ, {"LOCAL_MCP_TOKEN": "secret"}, clear=False):
            self.assertEqual(
                target._http_headers(),
                {"X-Static": "reviewed", "X-Local-Token": "secret"},
            )


if __name__ == "__main__":
    unittest.main()
