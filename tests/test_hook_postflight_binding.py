# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.
"""Real adapter/controller/outbox binding with credential-free Gateway fixtures."""

import asyncio
from dataclasses import replace
from hook_control_support import HookControlRuntimeCase
from atellagent_client.integrations.agents import host_hooks
from atellagent_client.integrations.agents.hook_control_protocol import HookControlError
from atellagent_client.integrations.agents.hook_outbox import HookOutcomeOutbox, HookOutcomeBindingMismatch


class HostPostflightBindingTests(HookControlRuntimeCase):
    def event(self, host, suffix="base"):
        return {"hook_event_name": "PreToolUse", "session_id": "binding-" + host,
                "tool_use_id": "binding-" + suffix, "tool_name": "Read" if host == "claude-code" else "view_image",
                "tool_input": {"file_path": "/fixture/source.txt"} if host == "claude-code" else {"path": "/fixture/source.png"},
                "cwd": "/fixture"}

    async def handle(self, host, event):
        # The fixture already owns a real local controller. Concurrent adapter
        # calls must use its transport without overlapping global patches.
        return await host_hooks.handle_host_hook(host, self.socket_path, event)

    async def rejected_then_original(self, host, event, changed):
        self.assertEqual((await self.handle(host, event)).exit_code, 0)
        before = len(self.governance.outcomes)
        result = await self.handle(host, {**changed, "hook_event_name": "PostToolUse", "tool_response": {"synthetic": True}})
        self.assertEqual(result.exit_code, 2)
        self.assertIn("outcome was not recorded", result.stderr)
        self.assertIn("Do not repeat", result.stderr)
        self.assertEqual(len(self.governance.outcomes), before)
        self.assertEqual(self.runtime._outbox.diagnostics()["awaiting_host"], 1)
        self.assertEqual((await self.handle(host, {**event, "hook_event_name": "PostToolUse", "tool_response": {"synthetic": True}})).exit_code, 0)
        self.assertNotIn("/fixture/source", self.runtime._outbox.path.read_text())

    async def test_changed_arguments_do_not_consume_either_host_reservation(self):
        for host in ["claude-code", "codex"]:
            with self.subTest(host=host):
                event = self.event(host)
                await self.rejected_then_original(host, event, {**event, "tool_input": {"path": "/fixture/protected"}})

    async def test_changed_tool_name_does_not_consume_reservation(self):
        for host in ["claude-code", "codex"]:
            with self.subTest(host=host):
                event = self.event(host)
                await self.rejected_then_original(host, event, {**event, "tool_name": "different_tool"})

    async def test_changed_codex_cwd_does_not_consume_reservation(self):
        event = self.event("codex")
        await self.rejected_then_original("codex", event, {**event, "cwd": "/different-workspace"})

    async def test_missing_input_is_not_inferred_from_call_id(self):
        for host in ["claude-code", "codex"]:
            with self.subTest(host=host):
                event = self.event(host)
                changed = dict(event); del changed["tool_input"]
                await self.rejected_then_original(host, event, changed)

    async def test_same_input_accepts_success_and_actual_claude_failure(self):
        for host, kind in [("codex", "PostToolUse"), ("claude-code", "PostToolUse"), ("claude-code", "PostToolUseFailure")]:
            with self.subTest(host=host, kind=kind):
                event = self.event(host, kind)
                self.assertEqual((await self.handle(host, event)).exit_code, 0)
                self.assertEqual((await self.handle(host, {**event, "hook_event_name": kind, "tool_response": {"synthetic": True}, "error": "synthetic failure"})).exit_code, 0)

    async def test_concurrent_wrong_and_correct_inputs_consume_once(self):
        event = self.event("claude-code")
        self.assertEqual((await self.handle("claude-code", event)).exit_code, 0)
        post = {**event, "hook_event_name": "PostToolUse", "tool_response": {"synthetic": True}}
        results = await asyncio.gather(self.handle("claude-code", {**post, "tool_input": {"file_path": "/wrong"}}), self.handle("claude-code", post))
        self.assertEqual(sorted(r.exit_code for r in results), [0, 2])
        self.assertEqual((await self.handle("claude-code", post)).exit_code, 2)

    async def test_codex_explicit_reference_is_bound_to_the_actual_updated_input(self):
        import json
        reference = "91f99348-7cc4-4253-ae25-d7f378aa6b0f"
        original = self.governance.preflight_async
        async def receipt(context):
            return replace(await original(context), assessment_reference=reference)
        self.governance.preflight_async = receipt
        event = {**self.event("codex"), "tool_name": "mcp__atellagent__atellagent_call_tool", "tool_input": {"server_id": "fixture"}}
        response = await self.handle("codex", event)
        self.assertEqual(response.exit_code, 0)
        updated = json.loads(response.stdout)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(updated, {"server_id": "fixture", "_atellagent_check": reference})
        post = {**event, "hook_event_name": "PostToolUse", "tool_response": {"synthetic": True}}
        self.assertEqual((await self.handle("codex", post)).exit_code, 2)
        self.assertEqual((await self.handle("codex", {**post, "tool_input": {**updated, "_atellagent_check": "customer-value"}})).exit_code, 2)
        self.assertEqual((await self.handle("codex", {**post, "tool_input": updated})).exit_code, 0)

    async def test_direct_rpc_cannot_omit_or_forge_observed_digest(self):
        event = {**self.event("claude-code"), "turn_id": "binding-turn"}
        self.assertEqual((await self.handle("claude-code", event)).exit_code, 0)
        params = {"host": "claude-code", "session_id": event["session_id"], "turn_id": "binding-turn",
                  "tool_call_id": event["tool_use_id"], "success": True}
        for value in [None, "z" * 64, "0" * 64]:
            with self.subTest(value=value):
                payload = dict(params)
                if value is not None: payload["observed_action_fingerprint"] = value
                with self.assertRaisesRegex(HookControlError, "outcome_binding_mismatch"):
                    await self.client.call("action.postflight", payload)
        self.assertEqual(self.runtime._outbox.diagnostics()["awaiting_host"], 1)

    async def test_restart_retains_binding_and_original_gateway_digest(self):
        box = HookOutcomeOutbox(self.runtime._outbox.path.parent / "separate-outcome.json")
        await box.reserve("synthetic-action", "a" * 64, postflight_binding_fingerprint="b" * 64)
        restored = HookOutcomeOutbox(box.path); await restored.load()
        with self.assertRaises(HookOutcomeBindingMismatch):
            await restored.record_outcome("synthetic-action", {"success": True}, observed_action_fingerprint="a" * 64)
        await restored.record_outcome("synthetic-action", {"success": True}, observed_action_fingerprint="b" * 64)
        entries = await restored.received_entries()
        self.assertEqual(entries[0][1]["binding_fingerprint"], "a" * 64)

    async def test_existing_outbox_reservation_requires_its_original_digest(self):
        import json
        path = self.runtime._outbox.path.parent / "previous-format.json"
        box = HookOutcomeOutbox(path)
        await box.reserve("synthetic-action", "a" * 64)
        payload = json.loads(path.read_text())
        del payload["entries"]["synthetic-action"]["postflight_binding_fingerprint"]
        path.write_text(json.dumps(payload)); path.chmod(0o600)
        restored = HookOutcomeOutbox(path); await restored.load()
        with self.assertRaises(HookOutcomeBindingMismatch):
            await restored.record_outcome("synthetic-action", {"success": True}, observed_action_fingerprint="b" * 64)
        await restored.record_outcome("synthetic-action", {"success": True}, observed_action_fingerprint="a" * 64)
