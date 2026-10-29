# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Credential-free contracts for the enrolled local hook-control runtime."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from atellagent_client.integrations.agents.hook_control import (
    HookControlClient,
    HookControlError,
    HookControlRuntime,
)
from atellagent_client.sdk.errors import PolicyTransportError, PolicyViolationError
from atellagent_client.integrations.agents.windows_named_pipe import (
    WINDOWS_NAMED_PIPE_PREFIX,
    WindowsNamedPipeServer,
    validate_windows_named_pipe,
    windows_named_pipe_request,
)


from hook_control_support import HookControlRuntimeCase, _config, _Participant, _Governance, _native_binding


class HookFailureDiagnosticsTests(unittest.TestCase):
    def test_windows_named_pipe_endpoint_accepts_only_local_flat_names(self) -> None:
        endpoint = r"\\.\pipe\atellagent-hook-control"
        self.assertEqual(validate_windows_named_pipe(endpoint), endpoint)
        for invalid in (
            r"\\server\pipe\atellagent-hook-control",
            r"\\.\pipe\atellagent\hook-control",
            WINDOWS_NAMED_PIPE_PREFIX,
            "/tmp/control.sock",
        ):
            with self.subTest(endpoint=invalid), self.assertRaises(ValueError):
                validate_windows_named_pipe(invalid)

    def test_windows_client_selects_the_validated_pipe_endpoint(self) -> None:
        endpoint = r"\\.\pipe\atellagent-hook-control"
        with patch(
            "atellagent_client.integrations.agents.hook_control_protocol._is_windows",
            return_value=True,
        ):
            client = HookControlClient(endpoint)
        self.assertEqual(client.socket_path, endpoint)


@unittest.skipUnless(os.name == "nt", "Windows named-pipe runtime test")
class WindowsNamedPipeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_pipe_round_trip_uses_the_local_json_lines_contract(self) -> None:
        endpoint = rf"\\.\pipe\atellagent-hook-control-test-{uuid4().hex}"

        async def handler(frame: bytes) -> dict[str, object]:
            request = json.loads(frame)
            return {"id": request["id"], "ok": True, "result": {"accepted": True}}

        server = WindowsNamedPipeServer(
            endpoint,
            handler,
            handler_timeout_seconds=2.0,
        )
        await server.start()
        try:
            response = await windows_named_pipe_request(
                endpoint,
                b'{"id":"test-request"}\n',
                timeout_seconds=2.0,
            )
        finally:
            server.close()
            await server.wait_closed()

        self.assertEqual(
            json.loads(response),
            {"id": "test-request", "ok": True, "result": {"accepted": True}},
        )

class HookGatewayFailureDiagnosticsTests(unittest.TestCase):
    def test_native_authority_errors_preserve_only_bounded_remediation(self):
        from atellagent_client.integrations.agents.control import ExternalAgentGovernance
        from atellagent_client.integrations.agents.hook_control_helpers import _controller_diagnostic_code
        cases = {
            "local_host_authorization_required": "native_delegation_required",
            "local_device_user_mismatch": "native_device_owner_mismatch",
            "device_binding_invalid": "native_device_binding_invalid",
            "device_inactive": "native_device_inactive",
            "device_proof_invalid": "native_device_proof_invalid",
            "device_proof_expired": "native_device_proof_expired",
            "device_proof_replayed": "native_device_proof_replayed",
        }
        for status in (401, 403):
            for code, diagnostic in cases.items():
                with self.subTest(status=status, code=code):
                    with self.assertRaises(PolicyViolationError) as caught:
                        ExternalAgentGovernance._raise_gateway_error(None, status,
                            {"detail": {"code": code, "message": "private diagnostic"}})
                    self.assertEqual(caught.exception.violation_type, code)
                    self.assertEqual(_controller_diagnostic_code(caught.exception), diagnostic)

    def test_remote_service_failure_is_distinct_and_does_not_expose_response_body(self):
        from atellagent_client.integrations.agents.control import ExternalAgentGovernance
        from atellagent_client.integrations.agents.hook_control import _safe_error_code
        from atellagent_client.integrations.agents.hook_control_helpers import _safe_http_status
        with self.assertRaises(PolicyTransportError) as caught:
            ExternalAgentGovernance._raise_gateway_error(None, 503, {"error": "private diagnostic"})
        self.assertEqual(_safe_error_code(caught.exception), "control_gateway_server_status")
        self.assertEqual(_safe_http_status(caught.exception), 503)
        self.assertNotIn("private diagnostic", str(caught.exception))

    def test_safe_gateway_remediation_codes_survive_without_response_detail(self):
        from atellagent_client.integrations.agents.control import ExternalAgentGovernance
        from atellagent_client.integrations.agents.hook_control import _controller_diagnostic_code

        with self.assertRaises(PolicyViolationError) as caught:
            ExternalAgentGovernance._raise_gateway_error(
                None, 403, {"code": "connection_required", "message": "private provider state"}
            )
        self.assertEqual(caught.exception.violation_type, "connection_required")
        self.assertEqual(_controller_diagnostic_code(caught.exception), "provider_connection_required")
        self.assertNotIn("private provider state", _controller_diagnostic_code(caught.exception) or "")

        with self.assertRaises(PolicyViolationError) as connection:
            ExternalAgentGovernance._raise_gateway_error(
                None, 409, {"error": "connection_required", "message": "private provider state"}
            )
        self.assertEqual(connection.exception.violation_type, "connection_required")

    def test_gateway_validation_failure_is_classified_without_private_detail(self):
        from atellagent_client.integrations.agents.control import ExternalAgentGovernance
        from atellagent_client.integrations.agents.hook_control import _safe_error_code

        with self.assertRaises(PolicyTransportError) as caught:
            ExternalAgentGovernance._raise_gateway_error(
                None, 422, {"detail": "private request-validation diagnostic"}
            )
        self.assertEqual(_safe_error_code(caught.exception), "control_gateway_client_status")
        self.assertNotIn("private", str(caught.exception))

    def test_gateway_user_authorization_code_preserves_sign_in_remediation(self):
        from atellagent_client.integrations.agents.control import ExternalAgentGovernance
        from atellagent_client.integrations.agents.hook_control import _safe_error_code

        with self.assertRaises(PolicyViolationError) as caught:
            ExternalAgentGovernance._raise_gateway_error(
                None, 403, {"detail": {"code": "local_host_authorization_expired"}}
            )
        self.assertEqual(_safe_error_code(caught.exception), "local_host_authorization_expired")

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
            _safe_outcome_delivery_error_code(
                PolicyViolationError("private receipt", "native_hook_outcome_receipt_expired")
            ),
            "outcome_delivery_receipt_expired",
        )
        self.assertEqual(
            _safe_outcome_delivery_error_code(RuntimeError("private response body")),
            "outcome_delivery_gateway_client_status",
        )




class HookControlRuntimeTests(HookControlRuntimeCase):
    async def test_health_identifies_delegation_without_claiming_cached_permission(self):
        health = await self.client.call("health", {})
        self.assertEqual(health["native_delegation"]["authorization_id"],
                         str(self.runtime._native_binding.authorization_id))
        self.assertEqual(health["native_delegation"]["authority_source"], "gateway_admission")
        with self.assertRaisesRegex(HookControlError, "unsupported_method"):
            await self.client.call("auth.reload", {})

    def test_runtime_rejects_a_binding_for_another_integration(self):
        from dataclasses import replace
        binding = replace(self.runtime._native_binding, request_id=uuid4())
        config = _config()
        with self.assertRaisesRegex(ValueError, "exact activated native binding"):
            HookControlRuntime(config, socket_path=self.socket_path, native_binding=binding)

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
        params = {**self._turn_fields(), "tool_call_id": "unmatched", "success": True, "observed_action_fingerprint": "a" * 64}
        self.runtime._outbox.action_key_for_host_correlation = AsyncMock(
            return_value="local:00000000-0000-0000-0000-000000000001:1:" + "a" * 64
        )
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
            HookControlRuntime(config, socket_path="/tmp/atellagent-hook-control.sock", native_binding=_native_binding(config))
        config = _config()
        config.capabilities = ["agent.control", "agent.process"]
        with self.assertRaisesRegex(ValueError, "only the provisioned agent.control"):
            HookControlRuntime(config, socket_path="/tmp/atellagent-hook-control.sock", native_binding=_native_binding(config))

    async def test_private_socket_and_health_discovery(self) -> None:
        socket_mode = stat.S_IMODE(Path(self.socket_path).stat().st_mode)
        parent_mode = stat.S_IMODE(Path(self.socket_path).parent.stat().st_mode)
        self.assertEqual(socket_mode, 0o600)
        self.assertEqual(parent_mode & 0o077, 0)
        health = await self.client.call("health", {})
        self.assertEqual(health["capabilities"], ["agent.control"])
        self.assertEqual(health["undelivered_outcomes"], 0)
        self.assertEqual(health["outcome_backlog"]["awaiting_host"], 0)
        self.assertEqual(health["outcome_backlog"]["received"], 0)
        self.assertIsNone(health["last_controller_diagnostic"])
        self.assertTrue(self.participant.started)

    async def test_health_exposes_only_bounded_remediation_state(self) -> None:
        self.runtime._last_controller_diagnostic = "user_grant_revoked"
        health = await self.client.call("health", {})
        self.assertEqual(health["last_controller_diagnostic"], "user_grant_revoked")
        self.assertNotIn("access_token", health["native_delegation"])

    async def test_missing_callbacks_expire_while_network_delivery_is_stalled(self):
        await self.runtime.stop()
        outbox = self.runtime._outbox
        await outbox.reserve("waiting", "a" * 64)
        await outbox.reserve("received", "b" * 64)
        await outbox.record_outcome(
            "received", {"outcome_observation": "result_observed", "success": None}
        )
        delivery_started = asyncio.Event()
        delivery_release = asyncio.Event()
        expired = asyncio.Event()

        async def blocked_delivery(**kwargs):
            delivery_started.set()
            await delivery_release.wait()

        original_prune = outbox.prune

        async def track_prune():
            await original_prune()
            if outbox.diagnostics()["awaiting_host"] == 0:
                expired.set()

        self.governance.native_hook_outcome_async = blocked_delivery
        with patch(
            "atellagent_client.integrations.agents.hook_control._OUTBOX_MAINTENANCE_INTERVAL_SECONDS", 0.01
        ), patch.object(outbox, "prune", side_effect=track_prune):
            await self.runtime.start()
            try:
                await asyncio.wait_for(delivery_started.wait(), timeout=1)
                with patch(
                    "atellagent_client.integrations.agents.hook_outbox.time",
                    Mock(time=Mock(return_value=time.time() + 901)),
                ):
                    await asyncio.wait_for(expired.wait(), timeout=1)
                    health = await self.client.call("health", {})
                    self.assertEqual(health["outcome_backlog"]["awaiting_host"], 0)
                    self.assertEqual(health["outcome_backlog"]["received"], 1)
                    self.assertFalse(delivery_release.is_set())
            finally:
                await self.runtime.stop()
        self.assertIsNone(self.runtime._outbox_maintenance_task)

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
