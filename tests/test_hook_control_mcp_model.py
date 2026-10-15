# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Credential-free contracts for the enrolled local hook-control runtime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from atellagent_client.integrations.agents.hook_control import (
    HookControlError,
    HookControlRuntime,
)
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError
from atellagent_client.protocol.agent_contracts import ModelDecision


from hook_control_support import HookControlRuntimeCase, _native_binding




class HookControlRuntimeTests(HookControlRuntimeCase):
    async def test_completed_hook_rpc_opt_in_timing_omits_tool_catalog(self) -> None:
        with patch(
            "atellagent_client.integrations.agents.hook_control_transport._TIMING_LOG_ENABLED",
            True,
        ), self.assertLogs(
            "atellagent_client.integrations.agents.hook_control", level="INFO"
        ) as captured:
            await self.client.call("mcp.list", {})
        timing = [
            line for line in captured.output
            if "hook_control_request_completed method=mcp.list" in line
        ]
        self.assertEqual(len(timing), 1)
        self.assertRegex(timing[0], r"elapsed_ms=\d+")
        self.assertNotIn("lookup", timing[0])

        with patch(
            "atellagent_client.integrations.agents.hook_control_transport._TIMING_LOG_ENABLED",
            False,
        ), self.assertNoLogs(
            "atellagent_client.integrations.agents.hook_control", level="INFO"
        ):
            await self.client.call("mcp.list", {})

    async def test_mcp_catalog_and_invocation_use_the_cluster_owned_assignment(self) -> None:
        await self.runtime.stop()
        with patch(
            "atellagent_client.integrations.agents.hook_control.ExternalAgentGovernance",
            return_value=self.governance,
        ):
            self.runtime = HookControlRuntime(
                self.config,
                socket_path=self.socket_path,
                participant=self.participant,  # type: ignore[arg-type]
                native_binding=_native_binding(self.config),
            )
        await self.runtime.start()
        catalog = await self.client.call("mcp.list", {})
        self.assertEqual(catalog["tools"][0]["name"], "atellagent_call_tool")
        self.assertNotIn("target_binding", catalog["tools"][0])
        result = await self.client.call(
            "mcp.invoke",
            {
                "tool_name": "atellagent_call_tool",
                "arguments": {"server_id": "server", "tool_id": "lookup", "arguments": {"record_id": "demo-001"}},
                "tool_call_id": "mcp-call-1",
            },
        )
        self.assertEqual(result["content"][0]["text"], "record")
        self.assertEqual(self.governance.mcp_calls[0]["target_binding"], "external-resource-id")
        self.assertEqual(self.governance.mcp_calls[0]["tool_name"], "provider_lookup")
        self.assertTrue(self.governance.mcp_calls[0]["action_key"].startswith("local:"))
        with self.assertRaisesRegex(HookControlError, "mcp_tool_not_assigned"):
            await self.client.call(
                "mcp.invoke",
                {
                    "tool_name": "unconfigured",
                    "arguments": {},
                    "tool_call_id": "mcp-call-2",
                },
            )

    async def test_mcp_response_withheld_by_policy_is_a_safe_tool_error(self) -> None:
        self.governance.mcp_communicate_async = AsyncMock(
            side_effect=PolicyViolationError(
                "private detector detail",
                "policy_violation",
                {"response_delivery_status": "withheld"},
            )
        )
        result = await self.client.call(
            "mcp.invoke",
            {
                "tool_name": "atellagent_call_tool",
                "arguments": {"server_id": "server", "tool_id": "lookup", "arguments": {"record_id": "fixture-pii-email"}},
                "tool_call_id": "mcp-response-withheld",
            },
        )
        self.assertTrue(result["is_error"])
        self.assertEqual(
            result["content"],
            [
                {
                    "type": "text",
                    "text": "Atellagent withheld this tool response under the configured policy.",
                }
            ],
        )

    async def test_mcp_policy_denial_is_a_safe_tool_error(self) -> None:
        self.governance.mcp_communicate_async = AsyncMock(
            side_effect=PolicyViolationError("private policy detail", "policy_denied")
        )
        result = await self.client.call(
            "mcp.invoke",
            {
                "tool_name": "atellagent_call_tool",
                "arguments": {"server_id": "server", "tool_id": "lookup", "arguments": {"record_id": "demo-001"}},
                "tool_call_id": "mcp-policy-denied",
            },
        )

        self.assertTrue(result["is_error"])
        self.assertEqual(
            result["content"],
            [
                {
                    "type": "text",
                    "text": "Atellagent blocked this tool call under the configured policy.",
                }
            ],
        )

    async def test_turn_entry_decision_never_synthesizes_provider_facts(self) -> None:
        result = await self.client.call(
            "model.decision",
            {
                **self._turn_fields(),
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(self.governance.model_request.input_scope, "turn_entry")
        self.assertIsNone(self.governance.model_request.provider)
        self.assertIsNone(self.governance.model_request.model)

    async def test_codex_hook_receipt_is_content_free_and_event_specific(self) -> None:
        await self.client.call(
            "model.decision",
            {
                **{**self._turn_fields(), "host": "codex"},
                "messages": [{"role": "user", "content": "secret prompt must not persist"}],
            },
        )
        self.assertEqual(len(self.participant.event_receipts), 1)
        receipt = self.participant.event_receipts[0]
        self.assertEqual(receipt["event_name"], "UserPromptSubmit")
        self.assertEqual(receipt["outcome"], "completed")
        self.assertIsInstance(receipt["elapsed_ms"], int)
        self.assertIn("observed_at", receipt)
        self.assertNotIn("secret", str(receipt).lower())

    async def test_codex_hook_timeout_reports_only_a_bounded_receipt_category(self) -> None:
        self.runtime.rpc_timeout_seconds = 0.01
        self.governance.decision_delay_seconds = 0.1
        with self.assertRaisesRegex(HookControlError, "control_timeout"):
            await self.client.call(
                "model.decision",
                {
                    **{**self._turn_fields(), "host": "codex"},
                    "messages": [{"role": "user", "content": "content is never retained"}],
                },
            )
        receipt = self.participant.event_receipts[0]
        self.assertEqual(receipt["event_name"], "UserPromptSubmit")
        self.assertEqual(receipt["outcome"], "timed_out")
        self.assertNotIn("content", str(receipt).lower())

    def test_hook_receipt_preserves_the_configured_host_timeout_window(self) -> None:
        from atellagent_client.integrations.agents.hook_health import HookEventHealth

        health = HookEventHealth()
        health.record(
            method="action.preflight",
            params={"host": "codex"},
            outcome="timed_out",
            elapsed_ms=20_500,
        )
        self.assertEqual(health.snapshot()[0]["elapsed_ms"], 20_500)
        health.record(
            method="action.preflight",
            params={"host": "codex"},
            outcome="timed_out",
            elapsed_ms=40_000,
        )
        self.assertEqual(health.snapshot()[0]["elapsed_ms"], 30_000)

    def test_claude_hook_health_preserves_all_four_lifecycle_receipts(self) -> None:
        from atellagent_client.integrations.agents.hook_health import HookEventHealth

        health = HookEventHealth()
        base_params = {"host": "claude-code"}
        for method, hook_event_name, outcome in (
            ("model.decision", "UserPromptSubmit", "completed"),
            ("action.preflight", "PreToolUse", "completed"),
            ("action.postflight", "PostToolUse", "completed"),
            ("action.postflight", "PostToolUseFailure", "failed"),
        ):
            health.record(
                method=method,
                params={**base_params, "hook_event_name": hook_event_name},
                outcome=outcome,
                elapsed_ms=11,
            )

        receipts = health.snapshot()
        self.assertEqual(
            [receipt["event_name"] for receipt in receipts],
            [
                "PostToolUse",
                "PostToolUseFailure",
                "PreToolUse",
                "UserPromptSubmit",
            ],
        )
        self.assertEqual(
            {receipt["outcome"] for receipt in receipts}, {"completed", "failed"}
        )
        self.assertNotIn("tool_input", str(receipts))

    def test_stale_hook_receipts_do_not_poison_future_heartbeats(self) -> None:
        from atellagent_client.integrations.agents.hook_health import HookEventHealth

        health = HookEventHealth()
        health.record(
            method="model.decision", params={"host": "codex"},
            outcome="completed", elapsed_ms=5,
        )
        health._receipts["UserPromptSubmit"]["observed_at"] = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()
        self.assertEqual(health.snapshot(), [])
        self.assertEqual(health._receipts, {})

        health.record(
            method="action.preflight", params={"host": "codex"},
            outcome="completed", elapsed_ms=5,
        )
        self.assertEqual(len(health.snapshot()), 1)

    async def test_full_request_decision_requires_explicit_hook_visible_target(self) -> None:
        self.governance.model = ModelDecision(
            **{
                **self.governance.model.__dict__,
                "input_scope": "full_model_request",
            }
        )
        result = await self.client.call(
            "model.decision",
            {
                **self._turn_fields(),
                "input_scope": "full_model_request",
                "messages": [{"role": "user", "content": "hello"}],
                "model": "gemini-2.5-pro",
                "provider": "google",
                "provider_request": {"config": {"temperature": 0.2}},
            },
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(self.governance.model_request.input_scope, "full_model_request")
        self.assertEqual(self.governance.model_request.provider, "google")
        self.assertEqual(
            self.governance.model_request.provider_request,
            {"config": {"temperature": 0.2}},
        )

    async def test_mismatched_model_decision_binding_fails_closed(self) -> None:
        async def mismatched(_request, **_kwargs):
            return self.governance.model

        self.governance.model_decision_async = mismatched
        with self.assertRaisesRegex(HookControlError, "decision_binding_invalid"):
            await self.client.call(
                "model.decision",
                {
                    **self._turn_fields(),
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

    async def test_model_decision_transport_failure_has_a_safe_distinct_code(self) -> None:
        async def unavailable(_request, **_kwargs):
            raise PolicyTransportError("unavailable")

        self.governance.model_decision_async = unavailable
        with self.assertRaisesRegex(
            HookControlError, "model_decision_transport_failure"
        ):
            await self.client.call(
                "model.decision",
                {
                    **self._turn_fields(),
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

    async def test_authority_fields_and_unsupported_obligations_fail_closed(self) -> None:
        with self.assertRaisesRegex(HookControlError, "unsupported_params_field"):
            await self.client.call(
                "model.decision",
                {
                    **self._turn_fields(),
                    "messages": [{"role": "user", "content": "hello"}],
                    "tenant_id": "attacker-selected",
                },
            )
        self.governance.model = ModelDecision(
            **{**self.governance.model.__dict__, "obligations": ({"type": "approval"},)}
        )
        with self.assertRaisesRegex(HookControlError, "unsupported_obligation"):
            await self.client.call(
                "model.decision",
                {**self._turn_fields(), "messages": [{"role": "user", "content": "hello"}]},
            )
