# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Credential-free contracts for the enrolled local hook-control runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from atellagent_client.integrations.agents.hook_control import (
    HookControlError,
    HookControlRuntime,
)
from atellagent_client.sdk.errors import PolicyViolationError


from hook_control_support import HookControlRuntimeCase, _config, _Participant, _Governance, _native_binding
from atellagent_client.pep.contracts import raw_action_fingerprint


def _callback_binding(preflight):
    return raw_action_fingerprint(action=preflight["tool_name"], facts=preflight["arguments"], raw_context={"host": preflight["host"], "adapter_version": preflight["adapter_version"]})




class HookControlRuntimeTests(HookControlRuntimeCase):
    async def test_concurrent_actions_are_isolated_and_postflight_retries(self) -> None:
        first = {
            **self._turn_fields(),
            "tool_call_id": "tool-1",
            "tool_name": "file.read",
            "arguments": {"path": "/workspace/a"},
            "adapter_version": "atellagent.host-hooks.v1",
        }
        second = {**first, "tool_call_id": "tool-2"}
        await asyncio.gather(
            self.client.call("action.preflight", first),
            self.client.call("action.preflight", second),
        )
        self.governance.outcome_failures = 1
        first_postflight = {
            "observed_action_fingerprint": _callback_binding(first),
            **self._turn_fields(),
            "tool_call_id": "tool-1",
        }
        second_postflight = {
            "observed_action_fingerprint": _callback_binding(second),
            **self._turn_fields(),
            "tool_call_id": "tool-2",
        }
        result = await self.client.call(
            "action.postflight",
            {**first_postflight, "success": True, "result_payload": {"ok": True}},
        )
        self.assertTrue(result["recorded"])
        self.assertGreaterEqual(len(self.governance.outcomes), 1)
        await self.client.call(
            "action.postflight",
            {
                **second_postflight,
                "success": False,
                "error_message": "failed",
                "error_type": "RuntimeError",
            },
        )

    async def test_delegation_replacement_preserves_original_delayed_outcome(self) -> None:
        from dataclasses import replace
        from uuid import uuid4
        original = self.runtime._native_binding
        delivered = asyncio.Event()

        async def capture_outcome(**kwargs) -> None:
            self.governance.outcomes.append(kwargs)
            delivered.set()

        self.governance.native_hook_outcome_async = capture_outcome
        preflight = {
            **self._turn_fields(),
            "tool_call_id": "account-switch-tool",
            "tool_name": "file.write",
            "arguments": {"path": "/workspace/result"},
            "adapter_version": "atellagent.host-hooks.v1",
        }
        preflight_result = await self.client.call("action.preflight", preflight)
        original_action_key = preflight_result["action_key"]
        self.runtime._native_binding = replace(original, authorization_id=uuid4())

        await self.client.call(
            "action.postflight",
            {
                **self._turn_fields(),
                "tool_call_id": "account-switch-tool",
                "observed_action_fingerprint": _callback_binding(preflight),
                "success": True,
                "result_payload": {"ok": True},
            },
        )
        await asyncio.wait_for(delivered.wait(), timeout=1)

        assert self.governance.outcomes[-1]["action_key"] == original_action_key
        assert str(original.authorization_id) in original_action_key
        assert str(self.runtime._native_binding.authorization_id) not in original_action_key

    async def test_restart_or_daemon_absence_never_returns_an_allow(self) -> None:
        await self.runtime.stop()
        with self.assertRaisesRegex(HookControlError, "control_socket_missing"):
            await self.client.call("health", {})
        await self.runtime.start()
        health = await self.client.call("health", {})
        self.assertEqual(health["undelivered_outcomes"], 0)

    async def test_gateway_revoked_delegation_cannot_be_overridden_locally(self) -> None:
        self.governance.preflight_async = AsyncMock(side_effect=PolicyViolationError(
            "Revoked", "local_host_authorization_revoked"))
        result = await self.client.call("action.preflight", {**self._turn_fields(),
            "tool_call_id": "revoked", "tool_name": "file.write", "arguments": {},
            "adapter_version": "atellagent.host-hooks.v1"})
        self.assertEqual(result, {"allowed": False, "reason_code": "local_host_authorization_revoked"})
        self.assertEqual(self.governance.action_gate.calls, [])

    async def test_gateway_outage_cannot_use_cached_observe_posture_as_authority(self) -> None:
        import httpx
        from time import time
        from atellagent_client.integrations.agents.native_hook_posture import NativeHookPosture
        self.governance.preflight_async = AsyncMock(side_effect=httpx.ConnectError("offline"))
        self.runtime._posture_cache._posture = NativeHookPosture("observe", "strict", "claude-code",
            "atellagent.host-hooks.v1", 1, int(time()) + 60)
        with self.assertRaisesRegex(HookControlError, "control_gateway_transport"):
            await self.client.call("action.preflight", {**self._turn_fields(),
                "tool_call_id": "offline", "tool_name": "file.read", "arguments": {},
                "adapter_version": "atellagent.host-hooks.v1"})

    async def test_second_runtime_cannot_replace_the_active_socket(self) -> None:
        competing_config = _config()
        with patch(
            "atellagent_client.integrations.agents.hook_control.ExternalAgentGovernance",
            return_value=_Governance(),
        ):
            competing = HookControlRuntime(
                competing_config,
                socket_path=self.socket_path,
                participant=_Participant(competing_config),  # type: ignore[arg-type]
                native_binding=_native_binding(competing_config),
            )
        with self.assertRaisesRegex(HookControlError, "control_runtime_already_running"):
            await competing.start()
        self.assertTrue(self.runtime.started)
        self.assertEqual((await self.client.call("health", {}))["capabilities"], ["agent.control"])

    async def test_stop_never_unlinks_a_replacement_socket(self) -> None:
        path = Path(self.socket_path)
        path.unlink()

        async def replacement(_reader, writer) -> None:
            writer.close()
            await writer.wait_closed()

        replacement_server = await asyncio.start_unix_server(replacement, path=self.socket_path)
        try:
            await self.runtime.stop()
            self.assertTrue(path.exists())
        finally:
            replacement_server.close()
            await replacement_server.wait_closed()
            if path.exists():
                path.unlink()

    async def test_reconstructed_runtime_rejects_cached_preflight_before_a_fresh_gate(self) -> None:
        """A new verifier cannot discard the persisted invocation reservation."""
        for host, tool, arguments in [
            ("codex", "apply_patch", {"command": "*** Begin Patch\n*** Add File: a.txt\n+synthetic\n*** End Patch"}),
            ("claude-code", "Read", {"file_path": "/workspace/a.txt", "limit": 1}),
        ]:
            params = {**self._turn_fields(), "host": host,
                      "tool_call_id": "reconstructed-" + host,
                      "tool_name": tool, "arguments": arguments,
                      "adapter_version": "atellagent.host-hooks.v1"}
            allowed = await self.client.call("action.preflight", params)
            self.assertTrue(allowed["allowed"])
            fresh_governance = _Governance()
            with patch(
                "atellagent_client.integrations.agents.hook_control.ExternalAgentGovernance",
                return_value=fresh_governance,
            ):
                reconstructed = HookControlRuntime(
                    self.config, socket_path=self.socket_path,
                    native_binding=self.runtime._native_binding,
                    participant=_Participant(self.config),
                )
            # Do not start a second socket runtime. Read the exact persisted
            # state with a fresh runtime/gate, as a replacement process does.
            await reconstructed._outbox.load()
            self.assertFalse(reconstructed.started)
            with self.assertRaisesRegex(HookControlError, "duplicate_tool_call"):
                await reconstructed._bounded_preflight(params)
            self.assertEqual(len(fresh_governance.preflights), 1)
            self.assertEqual(fresh_governance.action_gate.calls, [])
            self.assertEqual(
                reconstructed._outbox.diagnostics()["reserved_delivery_slots"],
                self.runtime._outbox.diagnostics()["reserved_delivery_slots"],
            )
            self.assertTrue(self.runtime.started)

    async def test_received_outcome_survives_a_restart_until_gateway_acknowledges(self) -> None:
        from atellagent_client.integrations.agents.hook_verification import load
        preflight = {
            **self._turn_fields(),
            "tool_call_id": "tool-unresolved",
            "tool_name": "file.write",
            "arguments": {"path": "/workspace/result"},
            "adapter_version": "atellagent.host-hooks.v1",
        }
        await self.client.call("action.preflight", preflight)
        postflight = {
            "observed_action_fingerprint": _callback_binding(preflight),
            **self._turn_fields(),
            "tool_call_id": "tool-unresolved",
            "success": True,
            "result_payload": {"ok": True},
        }
        self.governance.outcome_failures = 1
        result = await self.client.call("action.postflight", postflight)
        self.assertTrue(result["recorded"])
        await asyncio.sleep(0.02)
        self.assertEqual((await self.client.call("health", {}))["undelivered_outcomes"], 1)
        self.assertIsNone(load(self.runtime.socket_path))
        await self.runtime.stop()
        await self.runtime.start()
        self.runtime._outbox_wakeup.set()
        await asyncio.sleep(0.02)
        self.assertEqual((await self.client.call("health", {}))["undelivered_outcomes"], 0)
        self.assertEqual(load(self.runtime.socket_path)["request_id"], str(self.runtime._native_binding.request_id))

    async def test_permanently_rejected_outcome_is_discarded_without_retrying(self) -> None:
        from atellagent_client.integrations.agents.hook_verification import load
        preflight = {
            **self._turn_fields(),
            "tool_call_id": "tool-expired-outcome",
            "tool_name": "file.write",
            "arguments": {"path": "/workspace/result"},
            "adapter_version": "atellagent.host-hooks.v1",
        }
        await self.client.call("action.preflight", preflight)
        self.governance.native_hook_outcome_async = AsyncMock(
            side_effect=PolicyViolationError("private detail", "policy_violation")
        )
        result = await self.client.call(
            "action.postflight",
            {
                **self._turn_fields(),
                "tool_call_id": "tool-expired-outcome",
                "observed_action_fingerprint": _callback_binding(preflight),
                "success": True,
                "result_payload": {"ok": True},
            },
        )
        self.assertTrue(result["recorded"])
        await asyncio.sleep(0.02)
        self.assertEqual((await self.client.call("health", {}))["undelivered_outcomes"], 0)
        self.assertEqual(self.governance.native_hook_outcome_async.await_count, 1)
        self.assertIsNone(load(self.runtime.socket_path))
