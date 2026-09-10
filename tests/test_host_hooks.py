# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Golden contracts for supported external coding-host command hooks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tomllib
import unittest
from unittest.mock import MagicMock, patch

from atellagent_client.integrations.agents import codex_posttool
from atellagent_client.integrations.agents import host_hooks
from atellagent_client.integrations.agents.hook_control import HookControlError


_SOCKET = "/run/user/1000/atellagent/control.sock"


class HostHookAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.result = {"allowed": True}
        self.failure: Exception | None = None

    async def _call(self, _socket: str, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        if self.failure:
            raise self.failure
        return self.result

    async def _handle(self, host: str, event: dict):
        with patch(
            "atellagent_client.integrations.agents.host_hooks._call",
            side_effect=self._call,
        ):
            return await host_hooks.handle_host_hook(host, _SOCKET, event)

    async def test_claude_hook_preflight_and_postflight_still_work(self) -> None:
        pre = {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "tool_name": "Bash",
            "tool_input": {"command": "pwd"},
        }
        response = await self._handle("claude-code", pre)
        self.assertEqual(response.exit_code, 0)
        self.assertEqual(self.calls[-1][1]["host"], "claude_code")
        self.assertEqual(self.calls[-1][1]["arguments"], {"command": "pwd"})
        self.result = {"recorded": True}
        post = {**pre, "hook_event_name": "PostToolUseFailure", "error": "failed"}
        response = await self._handle("claude-code", post)
        self.assertEqual(response.exit_code, 0)
        self.assertFalse(self.calls[-1][1]["success"])

    async def test_gemini_hook_model_and_tool_paths_still_work(self) -> None:
        model = {
            "hook_event_name": "BeforeModel",
            "session_id": "session-1",
            "timestamp": "2026-08-14T23:15:00Z",
            "llm_request": {
                "model": "gemini-2.5-pro",
                "messages": [{"role": "model", "content": "hello"}],
            },
        }
        response = await self._handle("gemini-cli", model)
        self.assertEqual(response.exit_code, 0)
        self.assertEqual(self.calls[-1][1]["host"], "gemini_cli")
        self.assertEqual(self.calls[-1][1]["messages"], [{"role": "assistant", "content": "hello"}])
        tool = {
            "hook_event_name": "BeforeTool",
            "session_id": "session-1",
            "timestamp": "2026-08-14T23:15:01Z",
            "tool_name": "read_file",
            "tool_input": {"path": "/tmp/example.txt"},
        }
        response = await self._handle("gemini-cli", tool)
        self.assertEqual(response.exit_code, 0)
        self.assertFalse(self.calls[-1][1]["postflight_required"])

    async def test_codex_canonical_prompt_and_tool_golden(self) -> None:
        prompt = {
            "cwd": "/workspace",
            "hook_event_name": "UserPromptSubmit",
            "model": "gpt-5",
            "permission_mode": "default",
            "prompt": "explain this module",
            "session_id": "session-1",
            "transcript_path": None,
            "turn_id": "turn-1",
        }
        await self._handle("codex", prompt)
        self.assertEqual(self.calls[-1][1]["turn_id"], "turn-1")

        pre = {
            "cwd": "/workspace",
            "hook_event_name": "PreToolUse",
            "model": "gpt-5",
            "permission_mode": "default",
            "session_id": "session-1",
            "tool_input": {"command": "raw command"},
            "tool_name": "Bash",
            "tool_use_id": "tool-1",
            "transcript_path": None,
            "turn_id": "turn-1",
        }
        allowed = await self._handle("codex", pre)
        self.assertEqual(allowed.exit_code, 0)
        self.assertEqual(allowed.stdout, "")

        self.result = {"allowed": False}
        denied = await self._handle("codex", pre)
        rendered = json.loads(denied.stdout)["hookSpecificOutput"]
        self.assertEqual(rendered["hookEventName"], "PreToolUse")
        self.assertEqual(rendered["permissionDecision"], "deny")
        self.assertEqual(self.calls[-1][1]["arguments"], {"command": "raw command"})

        _, preflight = self.calls[-1]
        self.assertTrue(preflight["postflight_required"])
        self.assertEqual(
            preflight["adapter_version"], host_hooks.HOST_HOOK_ADAPTER_VERSION
        )
        # Codex correlates a post-tool result with its tool-use ID.  Its
        # result event need not repeat the optional turn ID from preflight.
        post = {
            key: value
            for key, value in pre.items()
            if key != "turn_id"
        }
        post.update({"hook_event_name": "PostToolUse", "tool_response": {"ok": True}})
        self.result = {"recorded": True}
        response = await self._handle("codex", post)
        self.assertEqual(response.exit_code, 0)
        method, postflight = self.calls[-1]
        self.assertEqual(method, "action.postflight")
        self.assertIsNone(postflight["success"])
        self.assertEqual(postflight["outcome_observation"], "result_observed")
        self.assertEqual(postflight["result_payload"], {"ok": True})

    async def test_codex_prompt_preserves_literal_user_content(self) -> None:
        """The hook must not add or strip presentation wrappers before policy."""

        prompts = (
            "Please explain the current policy module.",
            "## My request:\nPlease explain the current policy module.",
            '{"prompt":"Please explain the current policy module."}',
            "`Please explain the current policy module.`",
        )
        for index, prompt in enumerate(prompts):
            self.calls.clear()
            response = await self._handle(
                "codex",
                {
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": prompt,
                    "session_id": "session-1",
                    "turn_id": f"turn-{index}",
                },
            )
            self.assertEqual(response.exit_code, 0)
            method, payload = self.calls[-1]
            self.assertEqual(method, "model.decision")
            self.assertEqual(payload["messages"], [{"role": "user", "content": prompt}])

    async def test_large_posttool_result_is_recorded_as_bounded_evidence(self) -> None:
        event = {
            "hook_event_name": "PostToolUse",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_use_id": "tool-1",
            "tool_name": "view_image",
            "tool_input": {"path": "/workspace/image.png"},
            "tool_response": {"image": "x" * (host_hooks._MAX_POSTFLIGHT_RESULT_BYTES + 1)},
        }
        self.result = {"recorded": True}
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 0)
        _, postflight = self.calls[-1]
        result = postflight["result_payload"]
        self.assertTrue(result["result_truncated"])
        self.assertGreater(result["result_byte_length"], host_hooks._MAX_POSTFLIGHT_RESULT_BYTES)
        self.assertEqual(len(result["result_sha256"]), 64)

    async def test_timeout_daemon_failure_and_malformed_input_fail_closed(self) -> None:
        event = {"hook_event_name": "PreToolUse", "session_id": "s", "turn_id": "turn-1", "tool_use_id": "t", "tool_name": "Bash", "tool_input": {}}
        self.failure = asyncio.TimeoutError()
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.assertEqual(response.stdout, "")
        self.assertIn("timed out", response.stderr)

        self.failure = HookControlError("control_unavailable")
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.failure = HookControlError("control_socket_access_denied")
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.assertIn("socket access was denied", response.stderr)
        self.failure = HookControlError("model_decision_gateway_transport")
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.assertIn("could not be reached", response.stderr)
        self.failure = HookControlError("control_directive_timeout")
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.assertIn("authorization in time", response.stderr)
        response = await self._handle("codex", {"hook_event_name": "PreToolUse"})
        self.assertEqual(response.exit_code, 2)
        response = await host_hooks.handle_host_hook("codex", "relative.sock", event)
        self.assertEqual(response.exit_code, 2)

    async def test_unrecorded_postflight_is_an_adapter_failure(self) -> None:
        event = {
            "hook_event_name": "PostToolUse",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "tool_name": "Bash",
            "tool_input": {},
            "tool_response": {"ok": True},
        }
        self.result = {"recorded": False}
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)

    async def test_atellagent_mcp_facade_is_not_preflighted_twice(self) -> None:
        event = {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_use_id": "tool-1",
            "tool_name": "mcp__atellagent__protected_action",
            "tool_input": {},
        }
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 0)
        self.assertEqual(self.calls, [])

    def test_public_metadata_templates_and_docs_stay_in_sync(self) -> None:
        root = Path(__file__).resolve().parents[1]
        metadata = json.loads(
            (root / "examples/config/host-hook-capabilities.json").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata, host_hooks.host_hook_capabilities())
        for filename in ("claude-code-hooks.user.json", "claude-code-hooks.managed.json"):
            content = json.loads((root / "examples/config" / filename).read_text(encoding="utf-8"))
            self.assertEqual(set(content["hooks"]), {"UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure"})
        for filename in ("codex-hooks.user.toml", "codex-hooks.managed.toml"):
            content = tomllib.loads((root / "examples/config" / filename).read_text(encoding="utf-8"))
            self.assertEqual(
                set(content["hooks"]) & {"UserPromptSubmit", "PreToolUse", "PostToolUse"},
                {"UserPromptSubmit", "PreToolUse", "PostToolUse"},
            )
        docs = (root / "docs/HOST_HOOKS.md").read_text(encoding="utf-8")
        self.assertIn("turn_entry", docs)
        self.assertIn("Cowork", docs)
        self.assertIn("non-blocking", docs)

    def test_codex_posttool_launcher_relays_bytes_to_adapter_module(self) -> None:
        payload = b'{"hook_event_name":"PostToolUse","tool_response":{"ok":true}}'
        completed = MagicMock(returncode=0)
        with patch("sys.stdin") as stdin, patch(
            "atellagent_client.integrations.agents.codex_posttool.subprocess.run",
            return_value=completed,
        ) as run:
            stdin.buffer.read.return_value = payload
            with self.assertRaises(SystemExit) as exited:
                codex_posttool.main(["--socket", _SOCKET])
        self.assertEqual(exited.exception.code, 0)
        run.assert_called_once_with(
            [
                sys.executable,
                "-m",
                "atellagent_client.integrations.agents.host_hooks",
                "--host",
                "codex",
                "--socket",
                _SOCKET,
            ],
            input=payload,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
