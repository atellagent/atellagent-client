# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public DTO and transport contracts for synchronous model decisions."""

from __future__ import annotations

import unittest
import asyncio
from types import SimpleNamespace

from atellagent_client.protocol.agent_contracts import (
    GovernanceCallContext,
    GovernanceReceipt,
    ModelDecision,
    ModelDecisionRequest,
)
from atellagent_client.integrations.agents.control_actions import execute_async
from atellagent_client.integrations.agents.control_actions import preflight_async
from atellagent_client.governance import ActionDenied
from atellagent_client.sdk.config_models import SDKDeploymentConfig, ServiceAccountConfig


class ModelDecisionContractTests(unittest.TestCase):
    def test_turn_entry_never_serializes_provider_or_model(self) -> None:
        request = ModelDecisionRequest(
            input_scope="turn_entry",
            messages=[{"role": "user", "content": "hello"}],
        )
        self.assertEqual(
            request.to_payload(),
            {
                "input_scope": "turn_entry",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        with self.assertRaisesRegex(ValueError, "must not provide"):
            ModelDecisionRequest(
                input_scope="turn_entry",
                messages=[{"role": "user", "content": "hello"}],
                model="unavailable-to-hook",
            )
        with self.assertRaisesRegex(ValueError, "exactly one user message"):
            ModelDecisionRequest(
                input_scope="turn_entry",
                messages=[{"role": "system", "content": "not a user prompt"}],
            )
        with self.assertRaisesRegex(ValueError, "exactly one user message"):
            ModelDecisionRequest(
                input_scope="turn_entry",
                messages=[
                    {"role": "user", "content": "first"},
                    {"role": "user", "content": "second"},
                ],
            )

    def test_full_request_binds_provider_visible_request_options(self) -> None:
        request = ModelDecisionRequest(
            input_scope="full_model_request",
            messages=[{"role": "user", "content": "hello"}],
            model="gemini-2.5-pro",
            provider="google",
            provider_request={"config": {"temperature": 0.2}},
        )
        self.assertEqual(
            request.to_payload()["provider_request"],
            {"config": {"temperature": 0.2}},
        )
        with self.assertRaisesRegex(
            ValueError, "turn_entry must not provide provider_request"
        ):
            ModelDecisionRequest(
                input_scope="turn_entry",
                messages=[{"role": "user", "content": "hello"}],
                provider_request={"config": {}},
            )

    def test_response_projection_excludes_undocumented_internal_fields(self) -> None:
        decision = ModelDecision.from_payload(
            {
                "outcome": "allow",
                "enforcement": "advisory",
                "input_scope": "turn_entry",
                "evaluated": {"content": "evaluated", "model": "unevaluated"},
                "reason_code": "policy.advisory",
                "reason": "Policy evaluated in advisory mode.",
                "obligations": [{"type": "record"}],
                "decision_id": "decision-1",
                "correlation_id": "correlation-1",
                "request_fingerprint": "a" * 64,
                "engine_details": {"must_not": "escape"},
                "scores": {"must_not": "escape"},
            }
        )
        self.assertEqual(decision.outcome, "allow")
        self.assertEqual(decision.obligations, ({"type": "record"},))
        self.assertFalse(hasattr(decision, "engine_details"))
        self.assertFalse(hasattr(decision, "scores"))
        self.assertEqual(decision.request_fingerprint, "a" * 64)

    def test_connected_agent_requires_cluster_provisioned_identity_mode(self) -> None:
        kwargs = {
            "client_id": "client-id",
            "gateway_url": "https://gateway.example",
            "oauth_token_url": "https://auth.example/token",
            "oauth_jwks_url": "https://auth.example/jwks",
            "service_account_id": "service-account-id",
            "integration_id": "integration-id",
            "tenant_id": "tenant-id",
            "cert_path": "/tmp/cert.pem",
            "key_path": "/tmp/key.pem",
            "integration_type": "agent",
            "deployment": SDKDeploymentConfig(),
        }
        with self.assertRaisesRegex(ValueError, "identity_mode is required"):
            ServiceAccountConfig(**kwargs)
        config = ServiceAccountConfig(
            **kwargs,
            identity_mode="boundary_identity_only",
        )
        self.assertEqual(config.identity_mode, "boundary_identity_only")

    def test_boolean_receipt_cannot_execute_without_directive_verification(self) -> None:
        class Gate:
            async def enforce(self, **_kwargs):
                raise ActionDenied("remote_directive_unavailable")

        class Governance:
            config = SimpleNamespace(integration_type="agent")
            action_gate = Gate()

            async def preflight_async(self, _context):
                raise AssertionError("the supplied receipt must be used")

        called = False

        async def callback():
            nonlocal called
            called = True

        async def run():
            with self.assertRaisesRegex(ActionDenied, "remote_directive_unavailable"):
                await execute_async(
                    Governance(),
                    callback,
                    GovernanceCallContext(tool_name="host.write", action_key="action-1"),
                    receipt=GovernanceReceipt(
                        action_key="action-1",
                        allowed=True,
                        outcome="allow",
                        workflow_context={},
                    ),
                )

        asyncio.run(run())
        self.assertFalse(called)

    def test_federated_tool_preflight_requires_trusted_evidence_before_transport(self) -> None:
        class Governance:
            identity_mode = "federated_agent_identity"

            async def bootstrap_async(self, _identity):
                raise AssertionError("must not contact bootstrap without evidence")

        async def run():
            with self.assertRaisesRegex(Exception, "trusted identity evidence"):
                await preflight_async(
                    Governance(),
                    GovernanceCallContext(tool_name="host.write"),
                )

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
