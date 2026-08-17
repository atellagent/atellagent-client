# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Golden contracts for supported external coding-host command hooks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tomllib
import unittest
from unittest.mock import patch

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

    async def test_claude_user_prompt_golden_allow_and_deny(self) -> None:
        event = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "prompt": "inspect this repository",
            "cwd": "/workspace",
        }
        allowed = await self._handle("claude-code", event)
        self.assertEqual(allowed.exit_code, 0)
        self.assertEqual(allowed.stdout, "")
        method, params = self.calls[-1]
        self.assertEqual(method, "model.decision")
        self.assertEqual(params["host"], "claude_code")
        self.assertEqual(params["messages"], [{"role": "user", "content": event["prompt"]}])
        self.assertTrue(params["turn_id"].startswith("hook-"))

        self.result = {"allowed": False}
        denied = await self._handle("claude-code", event)
        self.assertEqual(denied.exit_code, 0)
        self.assertEqual(json.loads(denied.stdout)["decision"], "block")

    async def test_claude_tool_golden_and_postflight_correlation(self) -> None:
        pre = {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "tool_name": "Bash",
            "tool_input": {"command": "pwd"},
        }
        allowed = await self._handle("claude-code", pre)
        self.assertEqual(allowed.exit_code, 0)
        output = json.loads(allowed.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "allow")
        _, preflight = self.calls[-1]
        self.assertEqual(preflight["arguments"], {"command": "pwd"})

        post = {
            **pre,
            "hook_event_name": "PostToolUseFailure",
            "error": "command failed",
        }
        self.result = {"recorded": True}
        result = await self._handle("claude-code", post)
        self.assertEqual(result.exit_code, 0)
        method, postflight = self.calls[-1]
        self.assertEqual(method, "action.postflight")
        self.assertFalse(postflight["success"])
        self.assertEqual(postflight["turn_id"], preflight["turn_id"])
        self.assertEqual(postflight["tool_call_id"], preflight["tool_call_id"])

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
            "tool_input": "raw command",
            "tool_name": "shell",
            "tool_use_id": "tool-1",
            "transcript_path": None,
            "turn_id": "turn-1",
        }
        self.result = {"allowed": False}
        denied = await self._handle("codex", pre)
        rendered = json.loads(denied.stdout)["hookSpecificOutput"]
        self.assertEqual(rendered["hookEventName"], "PreToolUse")
        self.assertEqual(rendered["permissionDecision"], "deny")
        self.assertEqual(self.calls[-1][1]["arguments"], {"input": "raw command"})

        self.result = {"allowed": True}
        post = {**pre, "hook_event_name": "PostToolUse", "tool_response": {"ok": True}}
        self.result = {"recorded": True}
        response = await self._handle("codex", post)
        self.assertEqual(response.exit_code, 0)
        self.assertEqual(self.calls[-1][0], "action.postflight")
        self.assertTrue(self.calls[-1][1]["success"])

    async def test_gemini_before_model_and_before_tool_golden(self) -> None:
        model = {
            "hook_event_name": "BeforeModel",
            "session_id": "session-1",
            "timestamp": "2026-08-14T23:15:00Z",
            "llm_request": {
                "model": "gemini-2.5-pro",
                "messages": [
                    {"role": "system", "content": "Work carefully."},
                    {"role": "user", "content": "inspect this repository"},
                    {"role": "model", "content": "I will inspect it."},
                ],
                "config": {"temperature": 0.2},
                "toolConfig": {"mode": "AUTO"},
            },
        }
        allowed = await self._handle("gemini-cli", model)
        self.assertEqual(allowed.exit_code, 0)
        self.assertEqual(allowed.stdout, "")
        method, params = self.calls[-1]
        self.assertEqual(method, "model.decision")
        self.assertEqual(params["host"], "gemini_cli")
        self.assertEqual(params["input_scope"], "full_model_request")
        self.assertEqual(params["model"], "gemini-2.5-pro")
        self.assertEqual(params["provider"], "google")
        self.assertEqual(
            params["messages"],
            [
                {"role": "system", "content": "Work carefully."},
                {"role": "user", "content": "inspect this repository"},
                {"role": "assistant", "content": "I will inspect it."},
            ],
        )
        self.assertEqual(
            params["provider_request"],
            {"config": {"temperature": 0.2}, "toolConfig": {"mode": "AUTO"}},
        )

        tool = {
            "hook_event_name": "BeforeTool",
            "session_id": "session-1",
            "timestamp": "2026-08-14T23:15:01Z",
            "tool_name": "read_file",
            "tool_input": {"path": "/tmp/example.txt"},
        }
        self.result = {"allowed": False}
        denied = await self._handle("gemini-cli", tool)
        self.assertEqual(denied.exit_code, 0)
        self.assertEqual(json.loads(denied.stdout)["decision"], "deny")
        method, params = self.calls[-1]
        self.assertEqual(method, "action.preflight")
        self.assertFalse(params["postflight_required"])

    async def test_timeout_daemon_failure_and_malformed_input_fail_closed(self) -> None:
        event = {"hook_event_name": "PreToolUse", "session_id": "s", "tool_use_id": "t", "tool_name": "Bash", "tool_input": {}}
        self.failure = asyncio.TimeoutError()
        response = await self._handle("claude-code", event)
        self.assertEqual(response.exit_code, 2)
        self.assertEqual(response.stdout, "")
        self.assertIn("unavailable", response.stderr)

        self.failure = HookControlError("control_unavailable")
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
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
        response = await self._handle("claude-code", event)
        self.assertEqual(response.exit_code, 2)

    async def test_atellagent_mcp_facade_is_not_preflighted_twice(self) -> None:
        event = {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "tool_name": "mcp__atellagent__protected_action",
            "tool_input": {},
        }
        response = await self._handle("claude-code", event)
        self.assertEqual(response.exit_code, 0)
        self.assertEqual(self.calls, [])

    def test_public_metadata_templates_and_docs_stay_in_sync(self) -> None:
        root = Path(__file__).resolve().parents[1]
        metadata = json.loads(
            (root / "examples/config/host-hook-capabilities.json").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata, host_hooks.host_hook_capabilities())
        for filename in (
            "claude-code-hooks.user.json",
            "claude-code-hooks.managed.json",
        ):
            content = json.loads((root / "examples/config" / filename).read_text(encoding="utf-8"))
            self.assertEqual(set(content["hooks"]), {
                "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure"
            })
        for filename in ("codex-hooks.user.toml", "codex-hooks.managed.toml"):
            content = tomllib.loads((root / "examples/config" / filename).read_text(encoding="utf-8"))
            self.assertEqual(set(content["hooks"]) & {"UserPromptSubmit", "PreToolUse", "PostToolUse"}, {
                "UserPromptSubmit", "PreToolUse", "PostToolUse"
            })
        docs = (root / "docs/HOST_HOOKS.md").read_text(encoding="utf-8")
        self.assertIn("turn_entry", docs)
        self.assertIn("Cowork", docs)
        self.assertIn("non-blocking", docs)


if __name__ == "__main__":
    unittest.main()
