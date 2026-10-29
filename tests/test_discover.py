# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Current-user discovery is bounded, read-only, and secret-redacting."""

from __future__ import annotations

import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout

from atellagent_client.discovery import inventory as discovery
from atellagent_client.cli import discover as command
from atellagent_client.cli.native_hook_commands import command as hook_command


class DiscoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()

    def _write(self, path: Path, contents: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    def test_current_user_inventory_redacts_endpoint_commands_and_environment(self) -> None:
        codex = self.home / ".codex" / "config.toml"
        claude = self.home / ".claude" / "settings.json"
        claude_state = self.home / ".claude.json"
        self._write(codex, '''
[[hooks.PreToolUse]]
hooks = [{ type = "command", command = "atellagent-hook-adapter --host codex --socket /secret/socket" }]
[mcp_servers.atellagent]
command = "/secret/bin/bridge"
args = ["--token", "private-value"]
[mcp_servers.remote_tools]
url = "https://private.example/mcp?key=private-value"
''')
        self._write(claude, json.dumps({"hooks": {"PreToolUse": [{"hooks": [{
            "command": "atellagent-hook-adapter --host claude-code --socket /secret/claude"
        }]}]}, "env": {"TOKEN": "private-value"}}))
        self._write(claude_state, json.dumps({"mcpServers": {
            "local_tool": {"command": "/secret/bin/tool", "args": ["private-value"], "env": {"TOKEN": "private-value"}},
            "remote_tool": {"type": "sse", "url": "https://private.example/sse?key=private-value"},
        }}))
        original = [path.read_bytes() for path in (codex, claude, claude_state)]
        with patch.object(discovery.shutil, "which", return_value=None):
            report = discovery.discover(home=self.home)
        self.assertEqual(original, [path.read_bytes() for path in (codex, claude, claude_state)])
        self.assertFalse(report["project_selected"])
        codex_report, claude_report = report["hosts"]
        self.assertTrue(codex_report["atellagent_hooks_detected"])
        self.assertTrue(codex_report["atellagent_mcp_detected"])
        self.assertTrue(claude_report["atellagent_hooks_detected"])
        self.assertEqual(
            [(item["name"], item["transport"]) for item in codex_report["mcp_servers"]],
            [("atellagent", "stdio"), ("remote_tools", "http")],
        )
        self.assertEqual(
            [(item["name"], item["transport"]) for item in claude_report["mcp_servers"]],
            [("local_tool", "stdio"), ("remote_tool", "sse")],
        )
        serialized = json.dumps(report)
        for secret in ("private-value", "private.example", "/secret/", "TOKEN"):
            self.assertNotIn(secret, serialized)

    def test_project_is_only_read_when_explicitly_selected(self) -> None:
        self._write(self.project / ".codex" / "hooks.json", json.dumps({"hooks": {
            "PreToolUse": [{"hooks": [{"command": "atellagent-hook-adapter --host codex --socket /secret"}]}]
        }}))
        self._write(self.project / ".mcp.json", json.dumps({"mcpServers": {
            "team_tool": {"type": "http", "url": "https://private.example"}
        }}))
        self._write(self.home / ".claude.json", json.dumps({"projects": {str(self.project): {
            "mcpServers": {"personal_project_tool": {"command": "private-command"}}
        }}}))
        without_project = discovery.discover(home=self.home)
        self.assertFalse(without_project["hosts"][0]["atellagent_hooks_detected"])
        self.assertEqual([], without_project["hosts"][1]["mcp_servers"])
        selected = discovery.discover(home=self.home, project=self.project)
        self.assertTrue(selected["hosts"][0]["atellagent_hooks_detected"])
        self.assertEqual(
            {"personal_project_tool", "team_tool"},
            {entry["name"] for entry in selected["hosts"][1]["mcp_servers"]},
        )
        self.assertNotIn(str(self.project), json.dumps(selected))

    def test_symlink_invalid_and_oversized_config_are_not_read(self) -> None:
        secret = self.root / "secret.json"
        self._write(secret, '{"mcpServers":{"leaked":{"command":"secret"}}}')
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "settings.json").symlink_to(secret)
        self._write(self.home / ".codex" / "config.toml", "not = [valid")
        with patch.object(discovery, "_MAX_CONFIG_BYTES", 20):
            report = discovery.discover(home=self.home)
        self.assertEqual("unsupported", report["hosts"][1]["config_files"][0]["status"])
        self.assertEqual("invalid", report["hosts"][0]["config_files"][0]["status"])
        self.assertNotIn("leaked", json.dumps(report))

    def test_only_plain_bounded_server_names_are_displayed(self) -> None:
        self._write(self.home / ".claude.json", json.dumps({"mcpServers": {
            "safe-name": {"type": []},
            "bad\nname": {"command": "secret"},
            "oversized" * 30: {"command": "secret"},
        }}))
        report = discovery.discover(home=self.home)
        self.assertEqual(["safe-name"], [item["name"] for item in report["hosts"][1]["mcp_servers"]])
        self.assertEqual("unknown", report["hosts"][1]["mcp_servers"][0]["transport"])

    def test_cli_shows_human_inventory_and_explicit_project_without_secrets(self) -> None:
        self._write(self.home / ".codex" / "config.toml", '''
[mcp_servers.research]
url = "https://private.example/mcp?token=secret-value"
''')
        output, notice = io.StringIO(), io.StringIO()
        with patch.object(discovery.Path, "home", return_value=self.home):
            with redirect_stdout(output), redirect_stderr(notice):
                command.run(["--project", str(self.project)])
        self.assertIn("Inspecting selected project", notice.getvalue())
        self.assertIn(str(self.project), notice.getvalue())
        self.assertIn("Codex: detected", output.getvalue())
        self.assertIn("research · http", output.getvalue())
        self.assertIn("discovered, not approved", output.getvalue())
        self.assertNotIn("private.example", output.getvalue())
        self.assertNotIn("secret-value", output.getvalue())
        self.assertNotIn(str(self.project), output.getvalue())

    def test_json_inventory_is_explicit_and_remains_structured(self) -> None:
        output, notice = io.StringIO(), io.StringIO()
        with patch.object(discovery.Path, "home", return_value=self.home):
            with redirect_stdout(output), redirect_stderr(notice):
                command.run(["--json", "--project", str(self.project)])
        report = json.loads(output.getvalue())
        self.assertTrue(report["project_selected"])
        self.assertNotIn(str(self.project), output.getvalue())

    def test_inventory_never_connects_or_starts_discovered_servers(self) -> None:
        self._write(self.home / ".codex" / "config.toml", '''
[mcp_servers.remote]
url = "https://remote.example.test/mcp"
[mcp_servers.local]
command = "/some/executable"
''')
        with (
            patch("socket.socket.connect", side_effect=AssertionError("network access")),
            patch("socket.create_connection", side_effect=AssertionError("network access")),
            patch("urllib.request.urlopen", side_effect=AssertionError("network access")),
            patch("subprocess.Popen", side_effect=AssertionError("server launch")),
        ):
            report = discovery.discover(home=self.home)
        self.assertEqual(
            {"remote", "local"},
            {item["name"] for item in report["hosts"][0]["mcp_servers"]},
        )


if __name__ == "__main__":
    unittest.main()


class GuardedHookDiscoveryTests(unittest.TestCase):
    def test_current_desktop_commands_are_discovered_for_only_their_host(self):
        for platform in ("darwin", "win32"):
            for host in ("codex", "claude-code"):
                with self.subTest(platform=platform, host=host):
                    executable = ("/Applications/Company's app/atellagent-cli" if platform == "darwin"
                                  else r"C:\Users\O'Neil\Atellagent\atellagent-cli.exe")
                    endpoint = (f"/private/tmp/{host}.sock" if platform == "darwin"
                                else rf"\\.\pipe\atellagent-user-{host}")
                    handler = hook_command([executable, "hook", "--host", host, "--socket", endpoint],
                                           platform=platform, host=host)
                    value = {"PreToolUse": [{"hooks": [handler]}]}
                    with patch("subprocess.Popen", side_effect=AssertionError("hook launch")):
                        self.assertTrue(discovery._has_adapter(value, host=host))
                        other = "claude-code" if host == "codex" else "codex"
                        self.assertFalse(discovery._has_adapter(value, host=other))

    def test_unrelated_or_malformed_commands_do_not_report_hooks(self):
        for text in (
            "echo atellagent-cli hook --host codex --socket /secret",
            "echo atellagent-hook-adapter --host codex",
            "atellagent-cli mcp-bridge --host codex --socket /secret",
            "atellagent-cli hook --host codex-other --socket /secret",
            "'unterminated atellagent-cli hook --host codex",
            "powershell.exe -NoProfile -NonInteractive -EncodedCommand !invalid!",
            "powershell.exe -NoProfile -NonInteractive -EncodedCommand YQ==",
            "atellagent-cli hook --host codex --socket " + "x" * 65536,
        ):
            with self.subTest(command=text[:80]):
                self.assertFalse(discovery._has_adapter({"command": text}, host="codex"))

    def test_module_launcher_is_discovered_for_only_its_exact_host(self):
        value={"PreToolUse":[{"hooks":[{"command":"python -m atellagent_client.hook_launcher --host codex --socket /secret/socket"}]}]}
        self.assertTrue(discovery._has_adapter(value,host="codex"))
        self.assertFalse(discovery._has_adapter(value,host="claude-code"))
