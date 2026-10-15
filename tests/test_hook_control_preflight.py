# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Credential-free contracts for the enrolled local hook-control runtime."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import httpx
import json
import threading
import time
from unittest.mock import AsyncMock, Mock, patch

from atellagent_client.integrations.agents.hook_control import (
    HookControlClient,
    HookControlError,
)
from atellagent_client.sdk.errors import AuthenticationError, PolicyViolationError
from atellagent_client.integrations.agents.hook_control_protocol import HOOK_CONTROL_PROTOCOL, MAX_REQUEST_BYTES


from hook_control_support import HookControlRuntimeCase




class HookControlRuntimeTests(HookControlRuntimeCase):
    async def test_preflight_verifies_directive_and_binds_one_tool_id(self) -> None:
        params = {
            **self._turn_fields(),
            "tool_call_id": "tool-1",
            "tool_name": "shell.execute",
            "arguments": {"command": "pwd"},
            "adapter_version": "atellagent.host-hooks.v1",
        }
        result = await self.client.call("action.preflight", params)
        self.assertTrue(result["allowed"])
        self.assertEqual(
            self.governance.action_gate.calls[0]["correlation_id"], result["action_key"]
        )
        self.assertEqual(self.governance.action_gate.calls[0]["raw_context"], {
            "host": "claude-code",
            "adapter_version": "atellagent.host-hooks.v1",
        })
        self.assertEqual(self.governance.preflights[0].identity.bearer_token, None)
        with self.assertRaisesRegex(HookControlError, "duplicate_tool_call"):
            await self.client.call("action.preflight", params)

    async def test_codex_preflight_binds_host_cwd_into_directive_verification(self) -> None:
        params = {
            **self._turn_fields(),
            "host": "codex",
            "tool_call_id": "tool-codex-cwd",
            "tool_name": "view_image",
            "arguments": {"path": "/workspace/chart.png"},
            "adapter_version": "atellagent.host-hooks.v1",
            "host_cwd": "/workspace",
        }
        result = await self.client.call("action.preflight", params)
        self.assertTrue(result["allowed"])
        self.assertEqual(self.governance.action_gate.calls[0]["raw_context"], {
            "host": "codex",
            "adapter_version": "atellagent.host-hooks.v1",
            "host_cwd": "/workspace",
        })

    async def test_mismatched_gateway_receipt_fingerprint_cannot_release_action(self) -> None:
        preflight = self.governance.preflight_async

        async def mismatched_receipt(context):
            return replace(
                await preflight(context),
                action_binding_fingerprint="0" * 64,
            )

        self.governance.preflight_async = mismatched_receipt
        with self.assertRaisesRegex(HookControlError, "decision_binding_invalid"):
            await self.client.call("action.preflight", {
                **self._turn_fields(),
                "tool_call_id": "tool-mismatched-receipt",
                "tool_name": "Write",
                "arguments": {"file_path": "/workspace/src/handler.py", "content": "x"},
                "adapter_version": "atellagent.host-hooks.v1",
            })
        self.assertEqual(self.governance.action_gate.calls, [])

    async def test_directive_failure_and_control_timeout_do_not_return_an_allow(self) -> None:
        params = {
            **self._turn_fields(),
            "tool_call_id": "tool-directive-failure",
            "tool_name": "shell.execute",
            "arguments": {"command": "pwd"},
            "adapter_version": "atellagent.host-hooks.v1",
        }
        self.governance.action_gate.failure_code = "remote_directive_invalid"
        with self.assertRaisesRegex(HookControlError, "control_directive_rejected"):
            await self.client.call("action.preflight", params)
        self.governance.action_gate.failure_code = None
        self.runtime.rpc_timeout_seconds = 0.01
        self.governance.decision_delay_seconds = 0.1
        with self.assertRaisesRegex(HookControlError, "control_timeout"):
            await self.client.call(
                "model.decision",
                {**self._turn_fields(), "messages": [{"role": "user", "content": "slow"}]},
            )

    async def test_directive_timeout_is_bounded_and_failed_postflight_is_backgrounded(self) -> None:
        params = {
            **self._turn_fields(),
            "tool_call_id": "tool-directive-timeout",
            "tool_name": "shell.execute",
            "arguments": {"command": "pwd"},
            "adapter_version": "atellagent.host-hooks.v1",
        }
        self.governance.action_gate.delay_seconds = 0.1
        self.governance.outcome_failures = 10
        self.runtime.rpc_timeout_seconds = 0.1
        with patch(
            "atellagent_client.integrations.agents.hook_control_actions._DIRECTIVE_VERIFICATION_TIMEOUT_SECONDS",
            0.01,
        ), self.assertRaisesRegex(HookControlError, "control_directive_timeout"):
            await self.client.call("action.preflight", params)
        await asyncio.sleep(0.02)
        self.assertEqual((await self.client.call("health", {}))["undelivered_outcomes"], 1)

    async def test_policy_denial_returns_a_normal_hook_deny(self) -> None:
        params = {
            **self._turn_fields(),
            "tool_call_id": "tool-policy-denied",
            "tool_name": "shell.execute",
            "arguments": {"command": "pwd"},
            "adapter_version": "atellagent.host-hooks.v1",
        }
        self.governance.preflight_async = AsyncMock(
            side_effect=PolicyViolationError("blocked", "policy_denied")
        )

        result = await self.client.call("action.preflight", params)

        self.assertEqual(result, {"allowed": False, "reason_code": "policy_denied"})

    async def test_gateway_transport_and_auth_failures_have_specific_safe_codes(self) -> None:
        params = {
            **self._turn_fields(),
            "tool_call_id": "tool-gateway-failure",
            "tool_name": "shell.execute",
            "arguments": {"command": "pwd"},
            "adapter_version": "atellagent.host-hooks.v1",
        }
        for failure, code in (
            (httpx.ConnectError("private gateway address"), "control_gateway_transport"),
            (AuthenticationError("private credential detail"), "control_gateway_auth"),
        ):
            with self.subTest(code=code):
                self.governance.preflight_async = AsyncMock(side_effect=failure)
                with self.assertRaisesRegex(HookControlError, f"^{code}$"):
                    await self.client.call("action.preflight", params)

    async def test_decision_after_four_seconds_still_verifies_before_allow(self) -> None:
        original = self.governance.preflight_async

        async def delayed(context):
            await asyncio.sleep(4.1)
            return await original(context)

        self.governance.preflight_async = delayed
        result = await self.client.call("action.preflight", {
            **self._turn_fields(), "tool_call_id": "longer-decision",
            "tool_name": "shell.execute", "arguments": {"command": "pwd"},
            "adapter_version": "atellagent.host-hooks.v1",
        })
        self.assertTrue(result["allowed"])
        self.assertEqual(len(self.governance.action_gate.calls), 1)

    async def test_local_transport_stages_share_one_budget_and_close_on_timeout(self) -> None:
        reader = Mock(readline=AsyncMock())
        writer = Mock()

        async def connect(*_, **__):
            await asyncio.sleep(0.06)
            return reader, writer

        async def drain():
            await asyncio.sleep(0.06)

        writer.drain = drain
        with patch("asyncio.open_unix_connection", connect), self.assertRaisesRegex(
            HookControlError, "control_socket_timeout"
        ):
            await HookControlClient(self.socket_path, timeout_seconds=0.1).call("health", {})
        reader.readline.assert_not_awaited()
        writer.close.assert_called_once()

    async def test_oversized_serialized_request_never_connects(self) -> None:
        # Escaping and Unicode expansion count toward the wire limit, not just
        # the length of the supplied text. The rejected payload is never sent.
        for value in ("x" * MAX_REQUEST_BYTES, '"' * (MAX_REQUEST_BYTES // 2),
                      "\u2603" * (MAX_REQUEST_BYTES // 6)):
            with self.subTest(length=len(value)), patch("asyncio.open_unix_connection", new_callable=AsyncMock) as connect:
                with self.assertRaisesRegex(HookControlError, "^control_request_too_large$"):
                    await self.client.call("health", {"padding": value})
                connect.assert_not_awaited()

    async def test_exact_request_frame_limit_is_accepted(self) -> None:
        envelope = {"protocol_version": HOOK_CONTROL_PROTOCOL, "id": "hook-request",
                    "method": "health", "params": {"padding": ""}}
        overhead = len(json.dumps(envelope, separators=(",", ":")).encode()) + 1
        params = {"padding": "x" * (MAX_REQUEST_BYTES - overhead)}
        with patch.object(self.runtime, "_dispatch", new=AsyncMock(return_value={"accepted": True})) as dispatch:
            self.assertEqual(await self.client.call("health", params), {"accepted": True})
            dispatch.assert_awaited_once_with("health", params)

        with patch("asyncio.open_unix_connection", new_callable=AsyncMock) as connect:
            with self.assertRaisesRegex(HookControlError, "^control_request_too_large$"):
                await self.client.call("health", {"padding": params["padding"] + "x"})
            connect.assert_not_awaited()

    async def test_preflight_and_verification_share_one_total_budget(self) -> None:
        original = self.governance.preflight_async

        async def delayed(context):
            await asyncio.sleep(0.04)
            return await original(context)

        self.governance.preflight_async = delayed
        self.governance.action_gate.delay_seconds = 0.04
        with patch(
            "atellagent_client.integrations.agents.hook_control_actions._NATIVE_PREFLIGHT_TIMEOUT_SECONDS", 0.06
        ), self.assertRaisesRegex(HookControlError, "control_timeout"):
            await self.client.call("action.preflight", {
                **self._turn_fields(), "tool_call_id": "shared-budget",
                "tool_name": "shell.execute", "arguments": {"command": "pwd"},
                "adapter_version": "atellagent.host-hooks.v1",
            })
        self.assertFalse(self.runtime._reserving)

    async def test_expired_preflight_cannot_allow_after_suppressed_cancellation(self) -> None:
        async def late_verification(**_):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return None

        self.governance.action_gate.enforce = late_verification
        with patch(
            "atellagent_client.integrations.agents.hook_control_actions._NATIVE_PREFLIGHT_TIMEOUT_SECONDS", 0.03
        ), self.assertRaisesRegex(HookControlError, "control_timeout"):
            await self.client.call("action.preflight", {
                **self._turn_fields(), "tool_call_id": "late-verification",
                "tool_name": "shell.execute", "arguments": {"command": "pwd"},
                "adapter_version": "atellagent.host-hooks.v1",
            })
        self.assertFalse(self.runtime._reserving)

    async def test_storage_commit_after_timeout_cannot_release_action(self) -> None:
        started = threading.Event()
        release = threading.Event()
        original_write = self.runtime._outbox._write_sync

        def blocked_write(entries):
            started.set()
            if not release.wait(2):
                raise TimeoutError("test did not release storage")
            original_write(entries)

        with patch.object(self.runtime._outbox, "_write_sync", side_effect=blocked_write), patch(
            "atellagent_client.integrations.agents.hook_control_actions._NATIVE_PREFLIGHT_TIMEOUT_SECONDS", 0.1
        ):
            try:
                with self.assertRaisesRegex(HookControlError, "control_timeout"):
                    await self.client.call("action.preflight", {
                        **self._turn_fields(), "tool_call_id": "storage-timeout",
                        "tool_name": "shell.execute", "arguments": {"command": "pwd"},
                        "adapter_version": "atellagent.host-hooks.v1",
                    })
                self.assertTrue(started.is_set())
                self.assertFalse(self.governance.action_gate.calls)
                self.assertFalse(self.runtime._reserving)
            finally:
                release.set()
            # A completed disk write is not execution evidence or permission.
            self.assertEqual(await self.runtime._outbox.received_entries(), ())
        self.assertFalse(self.governance.action_gate.calls)
        self.assertEqual(self.runtime._outbox.diagnostics()["awaiting_host"], 1)

    async def test_blocking_verification_cannot_return_a_late_allow(self) -> None:
        async def late_verification(**_):
            time.sleep(0.04)

        self.governance.action_gate.enforce = late_verification
        with patch(
            "atellagent_client.integrations.agents.hook_control_actions._NATIVE_PREFLIGHT_TIMEOUT_SECONDS", 0.03
        ), self.assertRaisesRegex(HookControlError, "control_timeout"):
            await self.client.call("action.preflight", {
                **self._turn_fields(), "tool_call_id": "blocked-loop",
                "tool_name": "shell.execute", "arguments": {"command": "pwd"},
                "adapter_version": "atellagent.host-hooks.v1",
            })
