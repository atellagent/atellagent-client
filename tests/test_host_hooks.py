# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Golden contracts for supported external coding-host command hooks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tomllib
import unittest
from unittest.mock import MagicMock, patch

from atellagent_client.integrations.agents import codex_posttool
from atellagent_client.integrations.agents import host_hooks, host_hook_events
from atellagent_client.integrations.agents import hook_control_actions
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
            "atellagent_client.integrations.agents.host_hook_transport._call",
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
        self.assertEqual(self.calls[-1][1]["host"], "claude-code")
        self.assertEqual(self.calls[-1][1]["arguments"], {"command": "pwd"})
        self.result = {"recorded": True}
        post = {**pre, "hook_event_name": "PostToolUseFailure", "error": "failed"}
        response = await self._handle("claude-code", post)
        self.assertEqual(response.exit_code, 0)
        self.assertFalse(self.calls[-1][1]["success"])
        self.assertEqual(self.calls[-1][1]["hook_event_name"], "PostToolUseFailure")

    async def test_windows_named_pipe_endpoint_reaches_the_same_hook_adapter_contract(self) -> None:
        endpoint = r"\\.\pipe\atellagent-hook-control"
        event = {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "tool_name": "Bash",
            "tool_input": {"command": "pwd"},
        }
        with patch(
            "atellagent_client.integrations.agents.host_hook_transport._is_windows",
            return_value=True,
        ), patch(
            "atellagent_client.integrations.agents.host_hook_transport._call",
            side_effect=self._call,
        ):
            response = await host_hooks.handle_host_hook("codex", endpoint, event)

        self.assertEqual(response.exit_code, 0)
        self.assertEqual(self.calls[-1][0], "action.preflight")

    async def test_codex_forwards_the_control_reference_without_changing_business_input(self):
        reference = "91f99348-7cc4-4253-ae25-d7f378aa6b0f"
        self.result = {"allowed": True, "assessment_reference": reference}
        event = {"hook_event_name": "PreToolUse", "session_id": "session",
                 "tool_use_id": "tool", "tool_name": "mcp__example__read",
                 "tool_input": {"record_id": "demo"}}
        response = await self._handle("codex", event)
        output = json.loads(response.stdout)["hookSpecificOutput"]
        self.assertEqual(output, {"hookEventName": "PreToolUse", "permissionDecision": "allow",
            "updatedInput": {"record_id": "demo", "_atellagent_check": reference}})
        self.assertEqual(self.calls[-1][1]["arguments"], {"record_id": "demo"})
        self.assertEqual(event["tool_input"], {"record_id": "demo"})
        self.result["allowed"] = False
        response = await self._handle("codex", event)
        output = json.loads(response.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertNotIn("updatedInput", output)

    async def test_codex_goal_lifecycle_can_finish_without_local_control(self) -> None:
        event = {
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "tool_name": "update_goal",
        }
        for error_code in ("control_socket_missing", "unsupported_params_field"):
            self.failure = HookControlError(error_code)
            for status in ("blocked", "paused", "complete"):
                for hook_event_name in ("PreToolUse", "PostToolUse"):
                    with self.subTest(error_code=error_code, status=status,
                                      hook_event_name=hook_event_name):
                        response = await self._handle(
                            "codex", {**event, "hook_event_name": hook_event_name,
                                      "tool_input": {"status": status}}
                        )
                        self.assertEqual(response, host_hooks.HookAdapterResponse(exit_code=0))
                        self.assertEqual(self.calls[-1][0],
                                         "action.preflight" if hook_event_name == "PreToolUse"
                                         else "action.postflight")

        response = await host_hooks.handle_host_hook(
            "codex", "relative.sock", {**event, "hook_event_name": "PreToolUse",
                                      "tool_input": {"status": "complete"}}
        )
        self.assertEqual(response, host_hooks.HookAdapterResponse(exit_code=0))

    async def test_codex_goal_lifecycle_uses_policy_when_control_is_healthy(self) -> None:
        event = {
            "hook_event_name": "PreToolUse", "session_id": "session-1",
            "tool_use_id": "tool-1", "tool_name": "update_goal",
            "tool_input": {"status": "complete"},
        }
        self.result = {"allowed": False, "reason_code": "policy_denied"}
        response = await self._handle("codex", event)
        self.assertIn('"permissionDecision":"deny"', response.stdout)
        self.assertEqual(self.calls[-1][0], "action.preflight")

        self.failure = HookControlError("policy_denied")
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)

        self.failure = None
        self.result = {"allowed": True}
        response = await self._handle("codex", event)
        self.assertEqual(response, host_hooks.HookAdapterResponse(exit_code=0))

    async def test_codex_goal_lifecycle_bad_host_event_does_not_escape(self) -> None:
        event = {
            "hook_event_name": "PreToolUse", "session_id": "session-1",
            "tool_name": "update_goal", "tool_input": {"status": "paused"},
        }
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.assertEqual(self.calls, [])

    async def test_other_goal_calls_still_require_preflight(self) -> None:
        self.result = {"allowed": False}
        cases = (
            ("codex", "update_goal", {"status": "active"}),
            ("codex", "update_goal", {"status": "blocked", "extra": True}),
            ("codex", "update_goal", {"status": "complete", "extra": True}),
            ("codex", "update_goal", {"status": "Blocked"}),
            ("codex", "mcp__example__update_goal", {"status": "blocked"}),
            ("claude-code", "update_goal", {"status": "blocked"}),
            ("claude-code", "update_goal", {"status": "paused"}),
            ("claude-code", "update_goal", {"status": "complete"}),
        )
        for host, tool_name, arguments in cases:
            with self.subTest(host=host, tool_name=tool_name, arguments=arguments):
                self.calls.clear()
                response = await self._handle(host, {
                    "hook_event_name": "PreToolUse",
                    "session_id": "session-1",
                    "tool_use_id": "tool-1",
                    "tool_name": tool_name,
                    "tool_input": arguments,
                })
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.calls[0][0], "action.preflight")
                self.assertIn('"permissionDecision":"deny"', response.stdout)

    async def test_protocol_mismatch_still_denies_ordinary_tools_with_restart_guidance(self) -> None:
        self.failure = HookControlError("unsupported_params_field")
        response = await self._handle("codex", {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "tool_name": "exec_command",
            "tool_input": {"cmd": "pwd"},
        })
        self.assertEqual(response.exit_code, 2)
        self.assertIn("restart the local controller", response.stderr)
        response = await self._handle("codex", {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "tool_use_id": "tool-2",
            "tool_name": "update_goal",
            "tool_input": {"status": "blocked", "extra": True},
        })
        self.assertEqual(response.exit_code, 2)

    async def test_control_reference_cannot_overwrite_a_customer_argument(self):
        self.result = {"allowed": True, "assessment_reference": "91f99348-7cc4-4253-ae25-d7f378aa6b0f"}
        event = {"hook_event_name": "PreToolUse", "session_id": "session",
                 "tool_use_id": "tool", "tool_name": "mcp__example__read",
                 "tool_input": {"_atellagent_check": "customer-value"}}
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.assertEqual(response.stdout, "")

    async def test_only_preflight_timeouts_invite_one_unchanged_retry(self):
        event = {"hook_event_name": "PreToolUse", "session_id": "session",
                 "tool_use_id": "tool", "tool_name": "Bash",
                 "tool_input": {"command": "pwd"}}
        for code in ("semantic_action_review_timeout", "deny", "technical_hold"):
            self.result = {"allowed": False, "reason_code": code,
                           "reason": "untrusted remote prose"}
            response = await self._handle("codex", event)
            output = json.loads(response.stdout)["hookSpecificOutput"]
            self.assertEqual(output["permissionDecision"], "deny")
            reason = output["permissionDecisionReason"]
            self.assertEqual("retry" in reason, code == "semantic_action_review_timeout")
            self.assertNotIn("untrusted remote prose", reason)
        self.failure = HookControlError("control_timeout")
        response = await self._handle("codex", event)
        self.assertIn("unchanged tool call once", response.stdout)
        self.assertIn('"permissionDecision":"deny"', response.stdout)

    def test_gate_timeout_code_requires_a_technical_hold(self):
        from atellagent_client.integrations.agents.control_actions import _policy_gate_error
        for outcome in ("technical_hold", "deny"):
            error = _policy_gate_error({"decision": {
                "outcome": outcome, "violation_type": "semantic_action_review_timeout",
            }})
            self.assertEqual(error.violation_type,
                             "semantic_action_review_timeout" if outcome == "technical_hold" else "deny")

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
            "tool_response": {"image": "x" * (host_hook_events._MAX_POSTFLIGHT_RESULT_BYTES + 1)},
        }
        self.result = {"recorded": True}
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 0)
        _, postflight = self.calls[-1]
        result = postflight["result_payload"]
        self.assertTrue(result["result_truncated"])
        self.assertGreater(result["result_byte_length"], host_hook_events._MAX_POSTFLIGHT_RESULT_BYTES)
        self.assertEqual(len(result["result_sha256"]), 64)

    async def test_failure_messages_distinguish_input_response_and_adapter_errors(self) -> None:
        event = {"hook_event_name": "PreToolUse", "session_id": "s",
                 "tool_use_id": "t", "tool_name": "Bash", "tool_input": {}}
        cases = [
            ({**event, "tool_name": None}, None, {"allowed": True}, "invalid or incomplete host input"),
            (event, None, {"allowed": "true"}, "control response was invalid"),
            (event, RuntimeError("private detail must not appear"), {}, "hook adapter could not process"),
            (event, ValueError("private detail must not appear"), {}, "hook adapter could not process"),
            (event, HookControlError("control_unavailable"), {}, "local controller encountered an unexpected error"),
        ]
        for payload, failure, result, expected in cases:
            with self.subTest(expected=expected):
                self.failure = failure
                self.result = result
                response = await self._handle("codex", payload)
                self.assertEqual(response.exit_code, 2)
                self.assertEqual(response.stdout, "")
                self.assertIn(expected, response.stderr)
                self.assertNotIn("private detail", response.stderr)

    async def test_unmatched_postflight_reports_audit_failure_not_execution_denial(self) -> None:
        self.failure = HookControlError("unknown_tool_call")
        response = await self._handle("codex", {
            "hook_event_name": "PostToolUse", "session_id": "s",
            "turn_id": "turn-1", "tool_use_id": "t", "tool_name": "Bash",
            "tool_response": "synthetic result",
        })
        self.assertEqual(response.exit_code, 2)
        self.assertEqual(response.stdout, "")
        self.assertIn("outcome was not recorded", response.stderr)
        self.assertIn("Do not repeat", response.stderr)
        self.assertNotIn("request denied", response.stderr)

    async def test_timeout_daemon_failure_and_malformed_input_fail_closed(self) -> None:
        event = {"hook_event_name": "PreToolUse", "session_id": "s", "turn_id": "turn-1", "tool_use_id": "t", "tool_name": "Bash", "tool_input": {}}
        self.failure = asyncio.TimeoutError()
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 0)
        output = json.loads(response.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("timed out", output["permissionDecisionReason"])

        self.failure = HookControlError("control_unavailable")
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.failure = HookControlError("governed_action_decision_quota_exhausted")
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.assertIn("allowance is exhausted", response.stderr)
        self.assertNotIn("unavailable", response.stderr)
        self.failure = HookControlError("control_socket_access_denied")
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.assertIn("socket access was denied", response.stderr)
        self.failure = HookControlError("control_gateway_transport")
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.assertIn("remote control service could not be reached", response.stderr)
        for code, message in (
            ("local_host_authorization_required", "device owner's consent"),
            ("local_host_authorization_expired", "replacement authorization"),
            ("local_host_authorization_revoked", "does not restore it"),
            ("local_device_user_mismatch", "owners do not match"),
            ("device_binding_invalid", "device binding"),
            ("device_inactive", "device authorization is inactive"),
            ("device_proof_invalid", "device proof could not be verified"),
            ("device_proof_expired", "machine clock"),
            ("device_proof_replayed", "reused device proof"),
        ):
            with self.subTest(code=code):
                self.failure = HookControlError(code)
                response = await self._handle("codex", event)
                self.assertEqual(response.exit_code, 2)
                self.assertIn(message, response.stderr)
                self.assertNotIn("service login", response.stderr)
                self.assertNotIn("local control could not complete", response.stderr)
        self.failure = HookControlError("control_gateway_client_status")
        response = await self._handle("codex", event)
        self.assertIn("gateway rejected", response.stderr)
        self.failure = HookControlError("model_decision_gateway_transport")
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.assertIn("could not be reached", response.stderr)
        self.failure = HookControlError("control_directive_timeout")
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.assertIn("authorization in time", response.stderr)
        self.failure = HookControlError("control_request_too_large")
        response = await self._handle("codex", event)
        self.assertEqual(response.exit_code, 2)
        self.assertIn("exceeds the local size limit", response.stderr)
        self.assertNotIn("timed out", response.stderr)
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

    async def test_mcp_names_do_not_exempt_preflight_or_override_denial(self) -> None:
        for host in ("codex", "claude-code", "gemini-cli"):
            for name in (
                "mcp__atellagent__protected_action",
                "mcp__codex_apps__atellagent__protected_action",
                "mcp__renamed__protected_action",
            ):
                for allowed in (True, False):
                    with self.subTest(host=host, name=name, allowed=allowed):
                        self.calls.clear()
                        self.result = {"allowed": allowed}
                        event = {
                            "hook_event_name": "BeforeTool" if host == "gemini-cli" else "PreToolUse",
                            "session_id": "session-1",
                            "turn_id": "turn-1",
                            "tool_use_id": "tool-1",
                            "tool_name": name,
                            "timestamp": "2026-08-14T23:15:01Z",
                            "tool_input": {"record_id": "fixture"},
                            # Extra event fields are not route authentication.
                            "server_url": "https://atellagent.com/mcp",
                            "governed": True,
                        }
                        response = await self._handle(host, event)
                        self.assertEqual(response.exit_code, 0)
                        self.assertEqual(len(self.calls), 1)
                        method, params = self.calls[0]
                        self.assertEqual(method, "action.preflight")
                        self.assertEqual(params["tool_name"], name)
                        self.assertEqual(params["arguments"], {"record_id": "fixture"})
                        self.assertNotIn("server_url", params)
                        self.assertNotIn("governed", params)
                        if allowed:
                            self.assertEqual(response.stdout, "")
                        else:
                            output = json.loads(response.stdout)
                            self.assertEqual(
                                output["decision"] if host == "gemini-cli"
                                else output["hookSpecificOutput"]["permissionDecision"],
                                "deny",
                            )

    async def test_mcp_name_does_not_bypass_control_failure(self) -> None:
        self.failure = HookControlError("control_socket_missing")
        event = {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_use_id": "tool-1",
            "tool_name": "mcp__atellagent__protected_action",
            "tool_input": {},
        }
        for host in ("codex", "claude-code"):
            with self.subTest(host=host):
                self.calls.clear()
                response = await self._handle(host, event)
                self.assertEqual(response.exit_code, 2)
                self.assertEqual(len(self.calls), 1)

    async def test_mcp_names_retain_supported_postflight(self) -> None:
        self.result = {"recorded": True}
        for host in ("codex", "claude-code"):
            for name in ("mcp__atellagent__protected_action", "mcp__renamed__protected_action"):
                events = ("PostToolUse", "PostToolUseFailure") if host == "claude-code" else ("PostToolUse",)
                for event_name in events:
                    with self.subTest(host=host, name=name, event=event_name):
                        self.calls.clear()
                        response = await self._handle(host, {
                            "hook_event_name": event_name,
                            "session_id": "session-1",
                            "turn_id": "turn-1",
                            "tool_use_id": "tool-1",
                            "tool_name": name,
                            "tool_input": {},
                            "tool_response": {"ok": True},
                            "error": "fixture failure",
                        })
                        self.assertEqual(response.exit_code, 0)
                        self.assertEqual(len(self.calls), 1)
                        method, params = self.calls[0]
                        self.assertEqual(method, "action.postflight")
                        self.assertEqual(params["tool_call_id"], "tool-1")

    def test_public_metadata_templates_and_docs_stay_in_sync(self) -> None:
        root = Path(__file__).resolve().parents[1]
        metadata = json.loads(
            (root / "examples/config/host-hook-capabilities.json").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata, host_hooks.host_hook_capabilities())
        for filename in (
            "claude-code-hooks.user.json",
            "claude-code-hooks.managed.json",
            "claude-code-hooks.windows.json",
        ):
            content = json.loads((root / "examples/config" / filename).read_text(encoding="utf-8"))
            self.assertEqual(set(content["hooks"]), {"UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure"})
            self.assertTrue(all(
                hook["timeout"] == 20
                for entries in content["hooks"].values()
                for entry in entries
                for hook in entry["hooks"]
            ))
        for filename in (
            "codex-hooks.user.toml",
            "codex-hooks.managed.toml",
            "codex-hooks.macos.toml",
            "codex-hooks.windows.toml",
        ):
            content = tomllib.loads((root / "examples/config" / filename).read_text(encoding="utf-8"))
            self.assertEqual(
                set(content["hooks"]) & {"UserPromptSubmit", "PreToolUse", "PostToolUse"},
                {"UserPromptSubmit", "PreToolUse", "PostToolUse"},
            )
            self.assertTrue(all(
                hook["timeout"] == 20
                for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse")
                for entry in content["hooks"][event]
                for hook in entry["hooks"]
            ))
            expected_messages = {
                "UserPromptSubmit": "Atellagent: checking prompt",
                "PreToolUse": "Atellagent: checking tool action",
                "PostToolUse": "Atellagent: recording tool result",
            }
            for event, message in expected_messages.items():
                self.assertTrue(all(
                    hook["statusMessage"] == message
                    for entry in content["hooks"][event]
                    for hook in entry["hooks"]
                ))
        gemini = json.loads((root / "examples/config/gemini-cli-hooks.user.json").read_text(encoding="utf-8"))
        self.assertTrue(all(
            hook["timeout"] == 20000
            for entries in gemini["hooks"].values()
            for entry in entries
            for hook in entry["hooks"]
        ))
        self.assertLess(host_hooks.ADAPTER_DEADLINE_SECONDS, 20)
        self.assertLess(hook_control_actions._NATIVE_PREFLIGHT_TIMEOUT_SECONDS,
                        host_hooks.ADAPTER_DEADLINE_SECONDS)
        self.assertLess(hook_control_actions._PREFLIGHT_TIMEOUT_SECONDS,
                        hook_control_actions._NATIVE_PREFLIGHT_TIMEOUT_SECONDS)
        docs = (root / "docs/HOST_HOOKS.md").read_text(encoding="utf-8")
        self.assertIn("turn_entry", docs)
        self.assertIn("Cowork", docs)
        self.assertIn("non-blocking", docs)

    def test_codex_posttool_subprocess_has_no_module_reexecution_warning(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m",
             "atellagent_client.integrations.agents.codex_posttool", "--socket", _SOCKET],
            input=b"{}", capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn(b"invalid or incomplete host input", completed.stderr)
        self.assertNotIn(b"RuntimeWarning", completed.stderr)
        self.assertEqual(completed.stdout, b"")

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
                "-c",
                "from atellagent_client.integrations.agents.host_hooks import main; main()",
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
