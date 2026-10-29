# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Credential-free contracts for the enrolled local hook-control runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
from uuid import UUID
from datetime import datetime, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from atellagent_client.onboarding.device_identity import AdmittedDevice
from atellagent_client.onboarding.native_credential_record import StoredNativeCredential
from atellagent_client.onboarding.native_runtime_binding import NativeRuntimeBinding
from atellagent_client.onboarding.service_account_certificate_verify import VerifiedServiceAccountCredential

from atellagent_client.integrations.agents.hook_control import (
    HookControlClient,
    HookControlRuntime,
)
from atellagent_client.governance import ActionDenied
from atellagent_client.protocol.agent_contracts import GovernanceReceipt, ModelDecision
from atellagent_client.pep.contracts import raw_action_fingerprint
from atellagent_client.sdk.config_models import (
    SDKDeploymentConfig,
    ServiceAccountConfig,
)


def _config() -> ServiceAccountConfig:
    return ServiceAccountConfig(
        platform_origin="https://platform.example",
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


def _native_binding(config):
    """Identity-only socket fixture; participant/HTTP admission is substituted."""
    account, integration = UUID(config.service_account_id), UUID(config.integration_id)
    device = AdmittedDevice(UUID(config.tenant_id), uuid4(), uuid4(), Ed25519PrivateKey.generate(),
                            Path("/tmp/device.crt"), Path("/tmp/device.key"))
    material = VerifiedServiceAccountCredential(uuid4(), account, integration, "", "", "a" * 64,
                                                 datetime.now(timezone.utc))
    stored = StoredNativeCredential(material, Path(config.cert_path), Path(config.key_path))
    return NativeRuntimeBinding(device, uuid4(), account, integration, uuid4(), uuid4(), uuid4(),
                                uuid4(), 1, config.gateway_url, stored)


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

    def set_device_proof_provider(self, provider) -> None:
        self.device_proof_provider = provider

    def set_device_registration_refresher(self, refresher) -> None:
        self.device_registration_refresher = refresher

    def set_device_instance_key(self, device_id) -> None:
        self.device_id = device_id

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
                    "name": "atellagent_call_tool",
                    "description": "Look up a record.",
                    "inputSchema": {"type": "object", "properties": {}},
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
        raw_context = {
            field: context.request_payload[field]
            for field in ("host", "adapter_version", "host_cwd")
            if field in context.request_payload
        }
        return GovernanceReceipt(
            action_key=context.action_key,
            allowed=True,
            outcome="allow",
            decision_id="decision-tool-1",
            workflow_context={"tenant_id": "tenant-1"},
            control_directive="signed-directive",
            action_binding_fingerprint=raw_action_fingerprint(
                action=context.tool_name,
                facts=context.arguments,
                raw_context=raw_context,
            ),
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

    async def mcp_catalog_async(self, *, operation="definitions", arguments=None):
        if operation == "definitions":
            return self.mcp_catalog
        if operation == "call_tool":
            return {"success": True, "target_binding": "external-resource-id",
                    "target_tool_name": "provider_lookup"}
        return {"success": True, "servers": [{"server_id": "server", "name": "Example"}]}




class HookControlRuntimeCase(unittest.IsolatedAsyncioTestCase):
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
                native_binding=_native_binding(self.config),
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
            "host": "claude-code",
            "session_id": "session-1",
            "turn_id": "turn-1",
        }

    @staticmethod
    async def _close_server(server) -> None:
        server.close()
        await server.wait_closed()
