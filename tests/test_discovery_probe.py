# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""An MCP probe needs one selected source and attended consent."""

from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx2

from atellagent_client.cli import discovery_probe
from atellagent_client.cli import discovery_probe_transport
from atellagent_client.cli.discovery_probe_transport import _http_url, list_probe_tools


_REFERENCE_SERVER = Path(__file__).parent / "fixtures" / "modern_mcp_reference_server.py"


class DiscoveryProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"
        self.home.mkdir()

    def test_selected_source_is_exact_and_ambiguity_requires_choice(self) -> None:
        codex = self.home / ".codex" / "config.toml"
        codex.parent.mkdir()
        codex.write_text(
            '[mcp_servers.catalog]\ncommand = "python"\nargs = ["--secret", "sensitive-sentinel"]\n',
            encoding="utf-8",
        )
        project = Path(self.temporary.name) / "project"
        config = project / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text('[mcp_servers.catalog]\nurl = "https://example.test/private"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "multiple sources"):
            discovery_probe.select_target(
                host="codex", name="catalog", home=self.home, project=project,
            )
        target = discovery_probe.select_target(
            host="codex", name="catalog", home=self.home,
            project=project, source="codex-user",
        )
        self.assertEqual((target.source, target.transport), ("codex-user", "stdio"))
        self.assertNotIn("sensitive-sentinel", discovery_probe._destination(target))

    def test_declined_probe_does_not_open_transport(self) -> None:
        codex = self.home / ".codex" / "config.toml"
        codex.parent.mkdir()
        codex.write_text('[mcp_servers.catalog]\nurl = "https://example.test/private?token=sensitive-sentinel"\n', encoding="utf-8")
        output = io.StringIO()
        with (
            patch.object(discovery_probe.Path, "home", return_value=self.home),
            patch.object(discovery_probe.sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value="no"),
            patch.object(discovery_probe, "list_probe_tools") as transport,
            redirect_stdout(output),
        ):
            discovery_probe.run(["--host", "codex", "--name", "catalog"])
        transport.assert_not_called()
        self.assertIn("Probe cancelled", output.getvalue())
        self.assertNotIn("sensitive-sentinel", output.getvalue())
        self.assertNotIn("/private", output.getvalue())

    def test_cleartext_remote_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires HTTPS"):
            _http_url({"url": "http://example.test/mcp"})
        with self.assertRaisesRegex(ValueError, "invalid"):
            _http_url({"url": "https://user:secret@example.test/mcp"})

    def test_attended_probe_lists_names_without_exposing_configuration(self) -> None:
        codex = self.home / ".codex" / "config.toml"
        codex.parent.mkdir()
        codex.write_text(
            '[mcp_servers.catalog]\nurl = "https://example.test/private?token=sensitive-sentinel"\n',
            encoding="utf-8",
        )
        output = io.StringIO()
        with (
            patch.object(discovery_probe.Path, "home", return_value=self.home),
            patch.object(discovery_probe.sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value="catalog"),
            patch.object(discovery_probe, "list_probe_tools", new_callable=AsyncMock) as probe,
            redirect_stdout(output),
        ):
            probe.return_value = ["echo"]
            discovery_probe.run(["--host", "codex", "--name", "catalog"])
        probe.assert_awaited_once()
        self.assertIn("echo", output.getvalue())
        self.assertIn("discovered, not approved", output.getvalue())
        self.assertNotIn("sensitive-sentinel", output.getvalue())
        self.assertNotIn("/private", output.getvalue())

    def test_modern_stdio_probe_lists_tools_without_invoking_one(self) -> None:
        names = asyncio.run(list_probe_tools("stdio", {
            "command": sys.executable,
            "args": [str(_REFERENCE_SERVER)],
        }))
        self.assertEqual(names, ["echo"])

    def test_unresponsive_stdio_probe_has_short_deadline(self) -> None:
        started = monotonic()
        with patch.object(discovery_probe_transport, "_PROBE_TIMEOUT_SECONDS", 0.2):
            with self.assertRaises(TimeoutError):
                asyncio.run(list_probe_tools("stdio", {
                    "command": sys.executable,
                    "args": ["-c", "import time; time.sleep(5)"],
                }))
        # MCP's stdio cleanup allows a brief graceful child-process shutdown.
        self.assertLess(monotonic() - started, 4)

    def test_http_probe_sends_only_discovery_and_tool_list(self) -> None:
        methods = []

        def respond(request: httpx2.Request) -> httpx2.Response:
            payload = json.loads(request.content)
            methods.append(payload["method"])
            if payload["method"] == "server/discover":
                result = {
                    "resultType": "complete", "supportedVersions": ["2026-07-28"],
                    "cacheScope": "private", "ttlMs": 0, "capabilities": {"tools": {}},
                }
            elif payload["method"] == "tools/list":
                result = {
                    "resultType": "complete", "cacheScope": "private", "ttlMs": 0,
                    "tools": [{"name": "echo", "inputSchema": {"type": "object"}}],
                }
            else:
                raise AssertionError(f"unexpected MCP method: {payload['method']}")
            return httpx2.Response(
                200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result},
            )

        with patch.object(httpx2, "AsyncHTTPTransport", return_value=httpx2.MockTransport(respond)):
            names = asyncio.run(list_probe_tools("http", {"url": "https://example.test/mcp"}))
        self.assertEqual(names, ["echo"])
        self.assertEqual(methods, ["server/discover", "tools/list"])

    def test_sse_probe_uses_a_transport_without_oauth_or_tool_calls(self) -> None:
        class ListingClient:
            def __init__(self, _transport, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def list_tools(self, **_kwargs):
                return SimpleNamespace(
                    tools=[SimpleNamespace(name="catalog")], next_cursor=None,
                )

        with (
            patch("mcp.Client", ListingClient),
            patch("mcp.client.sse.sse_client", return_value=object()) as sse,
        ):
            names = asyncio.run(list_probe_tools("sse", {"url": "https://example.test/events"}))
        self.assertEqual(names, ["catalog"])
        self.assertEqual(sse.call_args.args[0], "https://example.test/events")
        client_factory = sse.call_args.kwargs["httpx_client_factory"]
        with self.assertRaisesRegex(ValueError, "OAuth"):
            client_factory(auth=object())


if __name__ == "__main__":
    unittest.main()
