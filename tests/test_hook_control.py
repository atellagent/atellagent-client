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
from atellagent_client.sdk.errors import PolicyViolationError
from atellagent_client.governance import ActionDenied
from atellagent_client.protocol.agent_contracts import GovernanceReceipt, ModelDecision
from atellagent_client.sdk.config_models import SDKDeploymentConfig, ServiceAccountConfig


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

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False


class _Gate:
    def __init__(self) -> None:
        self.calls = []
        self.failure_code = None

    async def enforce(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self.failure_code:
            raise ActionDenied(self.failure_code)


class _Governance:
    def __init__(self) -> None:
        self.action_gate = _Gate()
        self.preflights = []
        self.postflights = []
        self.postflight_failures = 0
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
            action_key="action-1",
            allowed=True,
            outcome="allow",
            decision_id="decision-tool-1",
            workflow_context={"tenant_id": "tenant-1"},
            control_directive="signed-directive",
        )

    async def postflight_async(self, context, **kwargs) -> None:
        self.postflights.append((context, kwargs))
        if self.postflight_failures:
            self.postflight_failures -= 1
            raise RuntimeError("temporary postflight failure")


class HookControlRuntimeTests(unittest.IsolatedAsyncioTestCase):
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
                postflight_attempts=2,
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
        self.assertEqual(health["unresolved_postflights"], 0)
        self.assertTrue(self.participant.started)

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
        }
        result = await self.client.call("action.preflight", params)
        self.assertTrue(result["allowed"])
        self.assertEqual(self.governance.action_gate.calls[0]["correlation_id"], "action-1")
        self.assertEqual(self.governance.preflights[0].identity.bearer_token, None)
        with self.assertRaisesRegex(HookControlError, "duplicate_tool_call"):
            await self.client.call("action.preflight", params)

    async def test_directive_failure_and_control_timeout_do_not_return_an_allow(self) -> None:
        params = {
            **self._turn_fields(),
            "tool_call_id": "tool-directive-failure",
            "tool_name": "shell.execute",
            "arguments": {"command": "pwd"},
        }
        self.governance.action_gate.failure_code = "remote_directive_invalid"
        with self.assertRaisesRegex(HookControlError, "remote_directive_invalid"):
            await self.client.call("action.preflight", params)
        self.governance.action_gate.failure_code = None
        self.runtime.rpc_timeout_seconds = 0.01
        self.governance.decision_delay_seconds = 0.1
        with self.assertRaisesRegex(HookControlError, "control_timeout"):
            await self.client.call(
                "model.decision",
                {**self._turn_fields(), "messages": [{"role": "user", "content": "slow"}]},
            )

    async def test_policy_denial_returns_a_normal_hook_deny(self) -> None:
        params = {
            **self._turn_fields(),
            "tool_call_id": "tool-policy-denied",
            "tool_name": "shell.execute",
            "arguments": {"command": "pwd"},
        }
        self.governance.preflight_async = AsyncMock(
            side_effect=PolicyViolationError("blocked", "opa_policy")
        )

        result = await self.client.call("action.preflight", params)

        self.assertEqual(result, {"allowed": False, "reason_code": "opa_policy"})

    async def test_concurrent_actions_are_isolated_and_postflight_retries(self) -> None:
        first = {
            **self._turn_fields(),
            "tool_call_id": "tool-1",
            "tool_name": "file.read",
            "arguments": {"path": "/workspace/a"},
        }
        second = {**first, "tool_call_id": "tool-2"}
        await asyncio.gather(
            self.client.call("action.preflight", first),
            self.client.call("action.preflight", second),
        )
        self.governance.postflight_failures = 1
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
        self.assertEqual(len(self.governance.postflights), 2)
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
        with self.assertRaisesRegex(HookControlError, "control_unavailable"):
            await self.client.call("health", {})
        await self.runtime.start()
        health = await self.client.call("health", {})
        self.assertEqual(health["unresolved_postflights"], 0)

    async def test_unresolved_postflight_remains_visible_and_retryable(self) -> None:
        preflight = {
            **self._turn_fields(),
            "tool_call_id": "tool-unresolved",
            "tool_name": "file.write",
            "arguments": {"path": "/workspace/result"},
        }
        await self.client.call("action.preflight", preflight)
        postflight = {
            **self._turn_fields(),
            "tool_call_id": "tool-unresolved",
            "success": True,
            "result_payload": {"ok": True},
        }
        self.governance.postflight_failures = 2
        result = await self.client.call("action.postflight", postflight)
        self.assertFalse(result["recorded"])
        self.assertEqual((await self.client.call("health", {}))["unresolved_postflights"], 1)
        result = await self.client.call("action.postflight", postflight)
        self.assertTrue(result["recorded"])


if __name__ == "__main__":
    unittest.main()
