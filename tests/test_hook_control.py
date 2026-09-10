# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Credential-free contracts for the enrolled local hook-control runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from atellagent_client.integrations.agents.hook_control import (
    HookControlClient,
    HookControlError,
    HookControlRuntime,
)
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError
from atellagent_client.governance import ActionDenied
from atellagent_client.protocol.agent_contracts import GovernanceReceipt, ModelDecision
from atellagent_client.sdk.config_models import SDKDeploymentConfig, ServiceAccountConfig


class HookFailureDiagnosticsTests(unittest.TestCase):
    def test_remote_service_failure_is_distinct_and_does_not_expose_response_body(self):
        from atellagent_client.integrations.agents.control import ExternalAgentGovernance
        from atellagent_client.integrations.agents.hook_control import _safe_error_code
        with self.assertRaises(PolicyTransportError) as caught:
            ExternalAgentGovernance._raise_gateway_error(None, 503, {"error": "private diagnostic"})
        self.assertEqual(_safe_error_code(caught.exception), "control_gateway_server_status")
        self.assertNotIn("private diagnostic", str(caught.exception))

    def test_failure_correlation_is_stable_without_exposing_arguments(self):
        from atellagent_client.integrations.agents.hook_control import _request_correlation
        fields = {"host": "codex", "session_id": "session", "turn_id": "turn", "tool_call_id": "call"}
        trace = _request_correlation(fields)
        self.assertEqual(trace, _request_correlation({**fields, "arguments": {"text": "private"}}))
        self.assertNotEqual(trace, _request_correlation({**fields, "tool_call_id": "other"}))
        self.assertEqual(len(trace), 24)

    def test_mcp_failure_correlation_omits_arguments(self):
        from atellagent_client.integrations.agents.hook_control import _request_correlation

        fields = {"tool_name": "mock_salesforce_read", "tool_call_id": "call-1"}
        trace = _request_correlation(fields)
        self.assertEqual(trace, _request_correlation({**fields, "arguments": {"record": "private"}}))
        self.assertNotEqual(trace, _request_correlation({**fields, "tool_call_id": "call-2"}))
        self.assertEqual(len(trace), 24)

    def test_outcome_delivery_diagnostics_are_bounded(self):
        from atellagent_client.integrations.agents.hook_control import (
            _safe_outcome_delivery_error_code,
        )

        server_error = PolicyTransportError("private provider response")
        server_error.safe_code = "control_gateway_server_status"
        self.assertEqual(
            _safe_outcome_delivery_error_code(server_error),
            "outcome_delivery_gateway_server_status",
        )
        self.assertEqual(
            _safe_outcome_delivery_error_code(
                PolicyViolationError("private policy detail", "policy_violation")
            ),
            "outcome_delivery_policy_rejected",
        )
        self.assertEqual(
            _safe_outcome_delivery_error_code(RuntimeError("private response body")),
            "outcome_delivery_gateway_client_status",
        )


def _config() -> ServiceAccountConfig:
    return ServiceAccountConfig(
        client_id="client-id",
        gateway_url="https://mtls.gateway.example",
        oauth_token_url="https://mtls.auth.example/token",
        oauth_jwks_url="https://mtls.auth.example/jwks",
        service_account_id=str(uuid4()),
        integration_id=str(uuid4()),
        tenant_id=str(uuid4()),
        capabilities=["agent.control"],
        cert_path="/tmp/client.crt",
        key_path="/tmp/client.key",
        integration_type="agent",
        identity_mode="boundary_identity_only",
        deployment=SDKDeploymentConfig(),
    )


class _Participant:
    def __init__(self, config: ServiceAccountConfig) -> None:
        self.config = config
        self.session = object()
        self.started = False
        self.event_receipts = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    def set_native_hook_event_receipts(self, *, event_receipts) -> None:
        self.event_receipts = event_receipts


class _Gate:
    def __init__(self) -> None:
        self.calls = []
        self.failure_code = None
        self.delay_seconds = 0.0

    async def enforce(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.failure_code:
            raise ActionDenied(self.failure_code)


class _Governance:
    def __init__(self) -> None:
        self.action_gate = _Gate()
        self.preflights = []
        self.outcomes = []
        self.outcome_failures = 0
        self.outcome_delay_seconds = 0.0
        self.mcp_calls = []
        self.mcp_catalog = {
            "tools": [
                {
                    "name": "lookup",
                    "description": "Look up a record.",
                    "input_schema": {"type": "object", "properties": {}},
                    "target_binding": "external-resource-id",
                    "target_tool_name": "provider_lookup",
                }
            ]
        }
        self.decision_delay_seconds = 0.0
        self.model = ModelDecision(
            outcome="allow",
            enforcement="enforced",
            input_scope="turn_entry",
            evaluated={"content": "evaluated"},
            reason_code="policy.allow",
            reason="Allowed.",
            obligations=(),
            valid_until=None,
            decision_id="decision-1",
            correlation_id="correlation-1",
            request_fingerprint="a" * 64,
        )

    async def model_decision_async(self, request, **_kwargs):
        self.model_request = request
        if self.decision_delay_seconds:
            await asyncio.sleep(self.decision_delay_seconds)
        return ModelDecision(
            **{
                **self.model.__dict__,
                "request_fingerprint": request.request_fingerprint,
            }
        )

    async def preflight_async(self, context):
        self.preflights.append(context)
        return GovernanceReceipt(
            action_key=context.action_key,
            allowed=True,
            outcome="allow",
            decision_id="decision-tool-1",
            workflow_context={"tenant_id": "tenant-1"},
            control_directive="signed-directive",
            action_binding_fingerprint="a" * 64,
        )

    async def native_hook_outcome_async(self, **kwargs) -> None:
        self.outcomes.append(kwargs)
        if self.outcome_delay_seconds:
            await asyncio.sleep(self.outcome_delay_seconds)
        if self.outcome_failures:
            self.outcome_failures -= 1
            raise RuntimeError("temporary outcome failure")

    async def mcp_communicate_async(self, **kwargs):
        self.mcp_calls.append(kwargs)
        return {
            "response": {
                "mcp_result": {
                    "content": [{"type": "text", "text": "record"}],
                }
            }
        }

    async def mcp_catalog_async(self):
        return self.mcp_catalog


class HookControlRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_codex_action_key_uses_session_scoped_tool_id(self):
        from atellagent_client.integrations.agents.hook_control import HookControlRuntime

        preflight_key = HookControlRuntime._action_key(
            host="codex",
            session_id="session-1",
            turn_id="turn-provided-at-preflight",
            tool_call_id="tool-1",
        )
        postflight_key = HookControlRuntime._action_key(
            host="codex",
            session_id="session-1",
            turn_id="hook-fallback-used-at-postflight",
            tool_call_id="tool-1",
        )

        self.assertEqual(preflight_key, postflight_key)

    async def test_postflight_distinguishes_missing_correlation_from_storage_failure(self):
        from atellagent_client.integrations.agents.hook_outbox import HookOutcomeCorrelationUnavailable
        params = {**self._turn_fields(), "tool_call_id": "unmatched", "success": True}
        for error, code in [(HookOutcomeCorrelationUnavailable("missing"), "unknown_tool_call"),
                            (ValueError("storage limit"), "outcome_delivery_unavailable"),
                            (OSError("disk failure"), "outcome_delivery_unavailable")]:
            with patch.object(self.runtime, "_record_local_outcome", new=AsyncMock(side_effect=error)):
                with self.assertRaisesRegex(HookControlError, code):
                    await self.runtime._postflight(params)

    def test_runtime_requires_the_enrolled_boundary_only_control_shape(self) -> None:
        config = _config()
        config.identity_mode = "federated_agent_identity"
        with self.assertRaisesRegex(ValueError, "boundary_identity_only"):
            HookControlRuntime(config, socket_path="/tmp/atellagent-hook-control.sock")
        config = _config()
        config.capabilities = ["agent.control", "agent.process"]
        with self.assertRaisesRegex(ValueError, "only the provisioned agent.control"):
            HookControlRuntime(config, socket_path="/tmp/atellagent-hook-control.sock")

    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup)
        self.socket_path = str(Path(self.directory.name) / "hooks" / "control.sock")
        self.config = _config()
        self.participant = _Participant(self.config)
        self.governance = _Governance()
        with patch(
            "atellagent_client.integrations.agents.hook_control.ExternalAgentGovernance",
            return_value=self.governance,
        ):
            self.runtime = HookControlRuntime(
                self.config,
                socket_path=self.socket_path,
                participant=self.participant,  # type: ignore[arg-type]
            )
        await self.runtime.start()
        self.client = HookControlClient(self.socket_path)

    async def _cleanup(self) -> None:
        if hasattr(self, "runtime"):
            await self.runtime.stop()
        self.directory.cleanup()

    @staticmethod
    def _turn_fields() -> dict[str, str]:
        return {
            "host": "claude_code",
            "session_id": "session-1",
            "turn_id": "turn-1",
        }

    async def test_private_socket_and_health_discovery(self) -> None:
        socket_mode = stat.S_IMODE(Path(self.socket_path).stat().st_mode)
        parent_mode = stat.S_IMODE(Path(self.socket_path).parent.stat().st_mode)
        self.assertEqual(socket_mode, 0o600)
        self.assertEqual(parent_mode & 0o077, 0)
        health = await self.client.call("health", {})
        self.assertEqual(health["capabilities"], ["agent.control"])
        self.assertEqual(health["undelivered_outcomes"], 0)
        self.assertTrue(self.participant.started)

    async def test_client_accepts_response_larger_than_default_stream_limit(self) -> None:
        response = (
            b'{"id":"hook-request","ok":true,"result":{"payload":"'
            + (b"x" * (70 * 1024))
            + b'"}}\n'
        )
        socket_path = str(Path(self.directory.name) / "large-response.sock")

        async def respond(_reader, writer) -> None:
            await _reader.readline()
            writer.write(response)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(respond, path=socket_path)
        self.addAsyncCleanup(self._close_server, server)
        result = await HookControlClient(socket_path).call("health", {})
        self.assertEqual(len(result["payload"]), 70 * 1024)

    async def test_client_labels_local_socket_access_denial(self) -> None:
        with patch(
            "atellagent_client.integrations.agents.hook_control_protocol.asyncio.open_unix_connection",
            side_effect=PermissionError(1, "Operation not permitted"),
        ), self.assertRaisesRegex(HookControlError, "control_socket_access_denied"):
            await self.client.call("health", {})

    @staticmethod
    async def _close_server(server) -> None:
        server.close()
        await server.wait_closed()

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
            )
        await self.runtime.start()
        catalog = await self.client.call("mcp.list", {})
        self.assertEqual(catalog["tools"][0]["name"], "lookup")
        self.assertNotIn("target_binding", catalog["tools"][0])
        result = await self.client.call(
            "mcp.invoke",
            {
                "tool_name": "lookup",
                "arguments": {"record_id": "demo-001"},
                "tool_call_id": "mcp-call-1",
            },
        )
        self.assertEqual(result["content"][0]["text"], "record")
        self.assertEqual(self.governance.mcp_calls[0]["target_binding"], "external-resource-id")
        self.assertEqual(self.governance.mcp_calls[0]["tool_name"], "provider_lookup")
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
                "tool_name": "lookup",
                "arguments": {"record_id": "fixture-pii-email"},
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
                "tool_name": "lookup",
                "arguments": {"record_id": "demo-001"},
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
        self.assertEqual(self.governance.preflights[0].identity.bearer_token, None)
        with self.assertRaisesRegex(HookControlError, "duplicate_tool_call"):
            await self.client.call("action.preflight", params)

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
            "atellagent_client.integrations.agents.hook_control._DIRECTIVE_VERIFICATION_TIMEOUT_SECONDS",
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
            **self._turn_fields(),
            "tool_call_id": "tool-1",
        }
        second_postflight = {
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

    async def test_restart_or_daemon_absence_never_returns_an_allow(self) -> None:
        await self.runtime.stop()
        with self.assertRaisesRegex(HookControlError, "control_socket_missing"):
            await self.client.call("health", {})
        await self.runtime.start()
        health = await self.client.call("health", {})
        self.assertEqual(health["undelivered_outcomes"], 0)

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

    async def test_received_outcome_survives_a_restart_until_gateway_acknowledges(self) -> None:
        preflight = {
            **self._turn_fields(),
            "tool_call_id": "tool-unresolved",
            "tool_name": "file.write",
            "arguments": {"path": "/workspace/result"},
            "adapter_version": "atellagent.host-hooks.v1",
        }
        await self.client.call("action.preflight", preflight)
        postflight = {
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
        await self.runtime.stop()
        await self.runtime.start()
        self.runtime._outbox_wakeup.set()
        await asyncio.sleep(0.02)
        self.assertEqual((await self.client.call("health", {}))["undelivered_outcomes"], 0)

    async def test_permanently_rejected_outcome_is_discarded_without_retrying(self) -> None:
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
                "success": True,
                "result_payload": {"ok": True},
            },
        )
        self.assertTrue(result["recorded"])
        await asyncio.sleep(0.02)
        self.assertEqual((await self.client.call("health", {}))["undelivered_outcomes"], 0)
        self.assertEqual(self.governance.native_hook_outcome_async.await_count, 1)


if __name__ == "__main__":
    unittest.main()
