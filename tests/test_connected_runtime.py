# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from atellagent_client.connected import (
    ConnectedBridge,
    ConnectedDelivery,
    ConnectedHandlerResult,
    ConnectedOperationHandler,
    ConnectedParticipant,
    ConnectedProtocolError,
    mount_agent_handler,
    mount_mcp_handler,
    mount_workflow_handler,
)
from atellagent_client.connected.mcp_client import (
    LocalMCPClient,
    _validate_discovery,
    _require_loopback_url,
)
from atellagent_client.connected.capability import (
    ConnectedCapabilityValidator,
)
from atellagent_client.connected.contracts import parse_connected_message
from atellagent_client.sdk import ConnectedSDKRuntime
from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.config_models import (
    BridgeDeploymentConfig,
    SDKDeploymentConfig,
)
from atellagent_client.sdk.gateway.session import GatewaySession


class _Response:
    def __init__(self, status_code: int, payload=None, *, http_version="HTTP/2"):
        self.status_code = status_code
        self._payload = payload
        self.http_version = http_version
        self.text = ""

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


class _Session:
    def __init__(self, config, responses):
        self.config = config
        self.base_url = "https://mtls.gateway.example"
        self.client = _Client(responses)

    async def get_authenticated_request_context(self):
        return self.client, {"Authorization": "Bearer oauth"}

    async def request_authenticated(self, method, url, *, headers=None, **kwargs):
        return await self.client.request(
            method,
            url,
            headers={"Authorization": "Bearer oauth", **dict(headers or {})},
            **kwargs,
        )

    async def close_async(self):
        return None


def _config(
    *,
    packaging="sdk",
    integration_type="agent",
) -> ServiceAccountConfig:
    return ServiceAccountConfig(
        client_id="client-id",
        gateway_url="https://mtls.gateway.example",
        oauth_token_url="https://mtls.auth.example/token",
        oauth_jwks_url="https://mtls.auth.example/jwks",
        service_account_id=str(uuid4()),
        integration_id=str(uuid4()),
        tenant_id=str(uuid4()),
        capabilities=["agent.invoke"],
        packaging=packaging,
        cert_path="/tmp/client.crt",
        key_path="/tmp/client.key",
        integration_type=integration_type,
        deployment=(
            BridgeDeploymentConfig()
            if packaging == "bridge"
            else SDKDeploymentConfig()
        ),
    )


def _message():
    return {
        "message": {
            "message_id": str(uuid4()),
            "kind": "action",
            "operation": "agent.process",
            "protocol_version": "v1",
            "execution_id": "execution-1",
            "execution_attempt_id": "attempt-1",
            "idempotency_key": "effect-1",
            "payload_schema": "atellagent.agent.process.v1",
            "payload": {"input": "hello"},
            "capability": "c" * 64,
            "lease": {
                "lease_id": str(uuid4()),
                "lease_token": "l" * 64,
                "attempt_number": 1,
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=60)
                ).isoformat(),
            },
        }
    }


class ConnectedRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_mcp_client_uses_the_pinned_reference_server_contract(self) -> None:
        reference_server = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "modern_mcp_reference_server.py"
        )
        client = LocalMCPClient(
            BridgeDeploymentConfig(
                target_transport="stdio",
                target_command=sys.executable,
                target_args=["-u", str(reference_server)],
            )
        )
        try:
            manifest = await client.manifest()
            response = await client.invoke(
                {
                    "jsonrpc": "2.0",
                    "id": "reference-call",
                    "method": "tools/call",
                    "params": {"name": "echo", "arguments": {}},
                },
                "reference-effect-1",
            )
        finally:
            await client.close()

        self.assertEqual(manifest["tools"][0]["name"], "echo")
        self.assertEqual(response["result"]["content"][0]["text"], "ok")

    def test_mcp_runtime_does_not_persist_a_wire_protocol_revision(self) -> None:
        config = _config(packaging="bridge", integration_type="mcp")
        self.assertFalse(hasattr(config, "mcp_protocol_version"))

    def test_sdk_and_bridge_are_thin_shapes_over_same_core(self) -> None:
        with patch(
            "atellagent_client.connected.participant.ConnectedCapabilityValidator"
        ):
            sdk = ConnectedSDKRuntime(
                _config(packaging="sdk"), session=SimpleNamespace()
            )
            bridge = ConnectedBridge(
                _config(packaging="bridge"), session=SimpleNamespace()
            )
        self.assertIsInstance(sdk, ConnectedParticipant)
        self.assertIsInstance(bridge, ConnectedParticipant)

    def test_consequential_handler_requires_target_idempotency(self) -> None:
        async def handler(_delivery, _actions):
            return ConnectedHandlerResult.succeeded(result_schema="result.v1")

        with self.assertRaisesRegex(ValueError, "propagate delivery.idempotency_key"):
            ConnectedOperationHandler(
                handler=handler,
                consequential=True,
                idempotency_mode="none",
            )

    async def test_mcp_mount_enforces_local_manifest_before_customer_handler(self) -> None:
        participant = SimpleNamespace(
            enforce_local_action=AsyncMock(),
            register_handler=Mock(),
        )
        customer_handler = AsyncMock(return_value={"jsonrpc": "2.0", "result": {}})
        mount_mcp_handler(
            participant,
            customer_handler,
            consequential=True,
            target_idempotent=True,
        )
        mounted = participant.register_handler.call_args.args[1]
        delivery = ConnectedDelivery(
            message_id=str(uuid4()),
            kind="action",
            operation="mcp.tools.call",
            execution_id=None,
            execution_attempt_id=None,
            idempotency_key="effect-local-1",
            payload_schema="atellagent.connected.mcp.v1",
            payload={
                "request": {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "file.write",
                        "arguments": {
                            "path": "/workspace/result.txt",
                            "content": "hello",
                        },
                    },
                }
            },
            delivery_attempt=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )

        await mounted(delivery, SimpleNamespace())

        participant.enforce_local_action.assert_awaited_once_with(
            action="file.write",
            correlation_id=delivery.message_id,
            facts={
                "path": "/workspace/result.txt",
                "access": "write",
                "bytes": 5,
            },
        )
        customer_handler.assert_awaited_once()

    async def test_certificate_reload_rebuilds_owned_tls_session(self) -> None:
        config = _config()
        first = SimpleNamespace(close_async=AsyncMock())
        second = SimpleNamespace(close_async=AsyncMock())
        with (
            patch(
                "atellagent_client.connected.participant.GatewaySession.from_service_account_config",
                side_effect=[first, second],
            ),
            patch(
                "atellagent_client.connected.participant.ConnectedCapabilityValidator"
            ),
        ):
            participant = ConnectedParticipant(config)
            await participant.reload_client_certificate()
        first.close_async.assert_awaited_once()
        self.assertIs(participant.session, second)

    async def test_supervised_rotation_prepares_activates_installs_and_reconnects(self) -> None:
        envelope = _message()["message"]
        envelope.update(
            {
                "kind": "control",
                "operation": "certificate.rotate",
                "payload_schema": "atellagent.connected.certificate-rotation.v1",
                "payload": {
                    "schema_version": "v1",
                    "reason": "certificate_expiry_window",
                    "current_certificate_public_key_sha256": "a" * 64,
                    "due_at": datetime.now(timezone.utc).isoformat(),
                    "deadline_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=5)
                    ).isoformat(),
                },
            }
        )
        message = parse_connected_message(envelope)
        operation_id = str(uuid4())
        prepared = SimpleNamespace(csr_pem="csr")
        staged = SimpleNamespace(
            certificate_expires_at=datetime.now(timezone.utc) + timedelta(days=30)
        )
        responses = [
            _Response(
                202,
                {
                    "operation_id": operation_id,
                    "status": "pending",
                    "operation_path": f"/rotations/{operation_id}",
                    "activation_path": f"/rotations/{operation_id}/activate",
                },
            ),
            _Response(
                200,
                {
                    "operation_id": operation_id,
                    "status": "issued",
                    "last_error": None,
                    "certificate": {
                        "certificate_pem": "leaf",
                        "certificate_chain_pem": "chain",
                    },
                },
            ),
            _Response(
                200,
                {
                    "operation_id": operation_id,
                    "status": "activated",
                    "certificate_public_key_sha256": "b" * 64,
                    "certificate_expires_at": staged.certificate_expires_at.isoformat(),
                    "certificate_rotation_due_at": (
                        staged.certificate_expires_at - timedelta(days=7)
                    ).isoformat(),
                    "certificate_rotation_deadline_at": staged.certificate_expires_at.isoformat(),
                },
            ),
        ]
        with (
            patch(
                "atellagent_client.connected.participant.ConnectedCapabilityValidator"
            ),
            patch(
                "atellagent_client.connected.participant.certificate_public_key_sha256",
                return_value="a" * 64,
            ),
            patch(
                "atellagent_client.connected.participant.prepare_certificate_rotation",
                return_value=prepared,
            ),
            patch(
                "atellagent_client.connected.participant.stage_certificate_rotation",
                return_value=staged,
            ) as stage,
        ):
            participant = ConnectedParticipant(_config(), session=SimpleNamespace())
            participant._instance_id = str(uuid4())
            participant._wait_for_other_deliveries = AsyncMock()
            participant._drain_for_certificate_rotation = AsyncMock()
            participant._rotation_request = AsyncMock(side_effect=responses)
            participant._install_activated_rotation = AsyncMock()
            result = await participant._perform_certificate_rotation(message)

        self.assertEqual(result.result_payload["status"], "reconnected")
        participant._drain_for_certificate_rotation.assert_awaited_once()
        participant._install_activated_rotation.assert_awaited_once_with(staged)
        stage.assert_called_once()
        self.assertEqual(
            [call.args[0] for call in participant._rotation_request.await_args_list],
            ["POST", "GET", "POST"],
        )
        activation_json = participant._rotation_request.await_args_list[2].kwargs["json"]
        self.assertEqual(activation_json["message_id"], message.message_id)
        self.assertEqual(activation_json["lease_id"], message.lease.lease_id)
        self.assertEqual(activation_json["lease_token"], message.lease.lease_token)

    async def test_delivery_projection_and_lifecycle_commits_result(self) -> None:
        response_message = _message()
        message_id = response_message["message"]["message_id"]
        lease_id = response_message["message"]["lease"]["lease_id"]
        responses = [
            _Response(200, response_message),
            _Response(
                200,
                {
                    "message_id": message_id,
                    "lease_id": lease_id,
                    "acknowledgement": "accepted",
                    "acknowledged_at": datetime.now(timezone.utc).isoformat(),
                },
            ),
            _Response(
                200,
                {
                    "message_id": message_id,
                    "result_id": str(uuid4()),
                    "terminal_status": "succeeded",
                    "committed_at": datetime.now(timezone.utc).isoformat(),
                },
            ),
        ]
        config = _config()
        session = _Session(config, responses)
        seen = {}

        async def handler(delivery, _actions):
            seen["delivery"] = delivery
            return ConnectedHandlerResult.succeeded(
                result_schema="atellagent.agent.result.v1",
                result_payload={"output": "ok"},
            )

        with patch(
            "atellagent_client.connected.participant.ConnectedCapabilityValidator"
        ) as validator_type:
            validator_type.return_value.validate = AsyncMock()
            participant = ConnectedParticipant(config, session=session)
            participant._instance_id = str(uuid4())
            participant.register_handler(
                "agent.process",
                handler,
                consequential=True,
                idempotency_mode="target",
            )
            await participant._receive_once()
            await asyncio.gather(*participant._delivery_tasks)

        delivery = seen["delivery"]
        self.assertEqual(delivery.idempotency_key, "effect-1")
        self.assertEqual(delivery.operation, "agent.process")
        self.assertEqual(delivery.delivery_attempt, 1)
        self.assertEqual([request[0] for request in session.client.requests], [
            "POST",
            "POST",
            "POST",
        ])
        acknowledgement = session.client.requests[1][2]["json"]
        self.assertEqual(acknowledgement["acknowledgement"], "accepted")
        result = session.client.requests[2][2]["json"]
        self.assertEqual(result["terminal_status"], "succeeded")

    async def test_sdk_and_bridge_share_registration_presence_and_drain_lifecycle(
        self,
    ) -> None:
        for packaging, runtime_type in (
            ("sdk", ConnectedSDKRuntime),
            ("bridge", ConnectedBridge),
        ):
            with self.subTest(packaging=packaging):
                instance_id = str(uuid4())
                responses = [
                    _Response(
                        200,
                        {
                            "instance_id": instance_id,
                            "protocol_version": "v1",
                            "presence_status": "online",
                            "registered_at": datetime.now(timezone.utc).isoformat(),
                            "receive_path": f"/v1/connected-runtimes/{instance_id}/receive",
                            "heartbeat_path": f"/v1/connected-runtimes/{instance_id}/heartbeat",
                            "drain_path": f"/v1/connected-runtimes/{instance_id}/drain",
                        },
                    ),
                    _Response(
                        200,
                        {
                            "instance_id": instance_id,
                            "presence_status": "online",
                            "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                        },
                    ),
                    _Response(
                        200,
                        {
                            "instance_id": instance_id,
                            "presence_status": "draining",
                            "drain_requested_at": datetime.now(
                                timezone.utc
                            ).isoformat(),
                        },
                    ),
                    _Response(204),
                ]
                config = _config(packaging=packaging)
                session = _Session(config, responses)
                with patch(
                    "atellagent_client.connected.participant.ConnectedCapabilityValidator"
                ):
                    runtime = runtime_type(config, session=session)

                await runtime._register()
                self.assertEqual(runtime.instance_id, instance_id)

                wait_calls = 0

                async def complete_one_heartbeat(awaitable, *, timeout):
                    nonlocal wait_calls
                    del timeout
                    wait_calls += 1
                    if wait_calls == 1:
                        awaitable.close()
                        raise asyncio.TimeoutError
                    runtime._stop_event.set()
                    await awaitable

                with (
                    patch(
                        "atellagent_client.connected.participant.asyncio.wait_for",
                        side_effect=complete_one_heartbeat,
                    ),
                    patch(
                        "atellagent_client.connected.participant.Path.read_bytes",
                        return_value=b"certificate",
                    ),
                    patch(
                        "atellagent_client.connected.participant.x509.load_pem_x509_certificate",
                        return_value=SimpleNamespace(
                            not_valid_after_utc=(
                                datetime.now(timezone.utc) + timedelta(days=30)
                            )
                        ),
                    ),
                    patch(
                        "atellagent_client.connected.participant.certificate_public_key_sha256",
                        return_value="a" * 64,
                    ),
                ):
                    await runtime._heartbeat_loop()

                runtime._stop_event.clear()
                runtime._started = True
                await runtime.stop()

                requests = session.client.requests
                self.assertEqual(
                    [request[0] for request in requests],
                    ["POST", "POST", "POST", "DELETE"],
                )
                self.assertEqual(
                    [request[1].rsplit("/", 1)[-1] for request in requests],
                    ["instances", "heartbeat", "drain", instance_id],
                )
                heartbeat_json = requests[1][2]["json"]
                self.assertEqual(
                    heartbeat_json["certificate_public_key_sha256"], "a" * 64
                )
                self.assertIn("certificate_expires_at", heartbeat_json)
                self.assertIsNone(runtime.instance_id)

    async def test_http2_is_required_without_transport_fallback(self) -> None:
        config = _config()
        session = _Session(config, [_Response(204, http_version="HTTP/1.1")])
        with patch(
            "atellagent_client.connected.participant.ConnectedCapabilityValidator"
        ):
            participant = ConnectedParticipant(config, session=session)
            participant._instance_id = str(uuid4())
            with self.assertRaisesRegex(ConnectedProtocolError, "HTTP/2"):
                await participant._receive_once()

    async def test_retry_uses_one_target_effect_with_stable_idempotency_key(self) -> None:
        config = _config()
        raw = _message()["message"]
        raw["payload"] = {
            "communication_metadata": {"communication_id": "effect-1"},
            "input": "hello",
        }
        delivery = parse_connected_message(raw).delivery()
        effects = {}

        async def target(payload):
            key = payload["communication_metadata"]["communication_id"]
            effects.setdefault(key, {"content": "once", "metadata": {}})
            return effects[key]

        with patch(
            "atellagent_client.connected.participant.ConnectedCapabilityValidator"
        ):
            participant = ConnectedParticipant(config, session=SimpleNamespace())
        mount_agent_handler(
            participant,
            target,
            consequential=True,
            target_idempotent=True,
        )
        registration = participant._handlers["agent.process"]
        first = await registration.handler(delivery, SimpleNamespace())
        second = await registration.handler(delivery, SimpleNamespace())
        self.assertEqual(len(effects), 1)
        self.assertEqual(first.result_payload, second.result_payload)

    def test_consequential_adapter_rejects_non_idempotent_target(self) -> None:
        with patch(
            "atellagent_client.connected.participant.ConnectedCapabilityValidator"
        ):
            participant = ConnectedParticipant(_config(), session=SimpleNamespace())
        with self.assertRaisesRegex(ValueError, "honors the delivery idempotency key"):
            mount_agent_handler(
                participant,
                lambda _payload: {},
                consequential=True,
                target_idempotent=False,
            )

    async def test_workflow_adapter_returns_canonical_cluster_outcome(self) -> None:
        class Handler:
            async def execute(self, payload):
                return {
                    "status": "completed",
                    "output": {"request_id": payload["request_id"]},
                }

            async def compile(self, _payload):
                return {"status": "compiled", "output": {}}

            async def resume(self, _payload):
                return {"status": "completed", "output": {}}

            async def cancel(self, _payload):
                return {"status": "cancelled", "output": {}}

        with patch(
            "atellagent_client.connected.participant.ConnectedCapabilityValidator"
        ):
            participant = ConnectedParticipant(_config(), session=SimpleNamespace())
        mount_workflow_handler(
            participant,
            Handler(),
            target_idempotent=True,
        )
        raw = _message()["message"]
        raw.update(
            {
                "operation": "workflow.execute",
                "payload_schema": "atellagent.connected.workflow-execute.v1",
                "payload": {
                    "protocol_version": "v1",
                    "operation": "execute",
                    "execution_id": "execution-1",
                    "deployment_id": "deployment-1",
                    "attempt_id": "attempt-1",
                    "runtime_input": {},
                    "workflow_context": {},
                },
            }
        )
        delivery = parse_connected_message(raw).delivery()
        result = await participant._handlers["workflow.execute"].handler(
            delivery, SimpleNamespace()
        )
        self.assertEqual(result.result_schema, "atellagent.connected.workflow-outcome.v1")
        self.assertEqual(result.result_payload["outcome"], "completed")
        self.assertEqual(result.result_payload["output"]["request_id"], "effect-1")

    async def test_rejected_cached_oauth_token_refreshes_exactly_once(self) -> None:
        client = _Client([_Response(401, {}), _Response(204, None)])
        auth = SimpleNamespace(invalidate_token=Mock())
        session = GatewaySession(
            config=_config(),
            auth_manager=auth,
            http_client_manager=SimpleNamespace(),
            oauth_http_client_manager=SimpleNamespace(),
            base_url="https://mtls.gateway.example",
        )
        session.get_authenticated_request_context = AsyncMock(
            side_effect=[
                (client, {"Authorization": "Bearer stale"}),
                (client, {"Authorization": "Bearer fresh"}),
            ]
        )
        response = await session.request_authenticated(
            "POST",
            "https://mtls.gateway.example/heartbeat",
            json={},
        )
        self.assertEqual(response.status_code, 204)
        auth.invalidate_token.assert_called_once_with()
        self.assertEqual(
            [request[2]["headers"]["Authorization"] for request in client.requests],
            ["Bearer stale", "Bearer fresh"],
        )

    async def test_local_mcp_adapter_binds_effect_key_to_tools_call(self) -> None:
        deployment = BridgeDeploymentConfig(
            target_transport="stdio",
            target_command="example-mcp",
        )
        target = LocalMCPClient(deployment)
        result_value = SimpleNamespace(
            model_dump=Mock(return_value={"content": [{"type": "text", "text": "ok"}]})
        )
        target._client = SimpleNamespace(
            call_tool=AsyncMock(return_value=result_value),
        )
        response = await target.invoke(
            {
                "jsonrpc": "2.0",
                "id": "request-1",
                "method": "tools/call",
                "params": {"name": "lookup", "arguments": {"id": 7}},
            },
            "effect-1",
        )
        target._client.call_tool.assert_awaited_once_with(
            "lookup",
            {"id": 7},
            meta={"atellagent/idempotencyKey": "effect-1"},
        )
        self.assertEqual(response["id"], "request-1")
        self.assertEqual(response["result"]["content"][0]["text"], "ok")

    async def test_local_mcp_transport_failure_is_not_retried(self) -> None:
        target = LocalMCPClient(
            BridgeDeploymentConfig(target_transport="stdio", target_command="example-mcp")
        )
        client = SimpleNamespace(call_tool=AsyncMock(side_effect=ConnectionError("lost")))
        target._client = client
        target.close = AsyncMock()

        with self.assertRaises(ConnectionError):
            await target.invoke(
                {
                    "jsonrpc": "2.0",
                    "id": "request-1",
                    "method": "tools/call",
                    "params": {"name": "lookup", "arguments": {}},
                },
                "effect-1",
            )

        client.call_tool.assert_awaited_once()
        target.close.assert_awaited_once()

    async def test_local_mcp_manifest_bypasses_discovery_cache(self) -> None:
        target = LocalMCPClient(
            BridgeDeploymentConfig(target_transport="stdio", target_command="example-mcp")
        )
        listed_tool = SimpleNamespace(
            model_dump=Mock(return_value={"name": "lookup", "inputSchema": {}})
        )
        client = SimpleNamespace(
            list_tools=AsyncMock(return_value=SimpleNamespace(tools=[listed_tool]))
        )
        target._client = client

        manifest = await target.manifest()

        client.list_tools.assert_awaited_once_with(cache_mode="bypass")
        self.assertEqual(manifest, {"tools": [{"name": "lookup", "inputSchema": {}}]})

    def test_local_mcp_requires_a_complete_modern_discovery_result(self) -> None:
        _validate_discovery(
            {
                "resultType": "complete",
                "supportedVersions": ["2026-07-28"],
                "cacheScope": "private",
                "ttlMs": 0,
                "capabilities": {},
            }
        )
        with self.assertRaisesRegex(ValueError, "does not support"):
            _validate_discovery(
                {
                    "resultType": "complete",
                    "supportedVersions": ["2025-06-18"],
                    "cacheScope": "private",
                    "ttlMs": 0,
                    "capabilities": {},
                }
            )

    def test_local_mcp_http_target_is_loopback_only(self) -> None:
        self.assertEqual(
            _require_loopback_url("http://127.0.0.1:9000/mcp"),
            "http://127.0.0.1:9000/mcp",
        )
        with self.assertRaisesRegex(ValueError, "loopback"):
            _require_loopback_url("https://mcp.example.com/mcp")

    def test_local_mcp_http_auth_is_loaded_only_from_environment(self) -> None:
        target = LocalMCPClient(
            BridgeDeploymentConfig(
                target_transport="http",
                target_url="http://127.0.0.1:9000/mcp",
                upstream_headers={"X-Static": "reviewed"},
                upstream_auth_header="X-Local-Token",
                upstream_auth_token_env="LOCAL_MCP_TOKEN",
            )
        )
        with patch.dict(os.environ, {"LOCAL_MCP_TOKEN": "secret"}, clear=False):
            self.assertEqual(
                target._http_headers(),
                {"X-Static": "reviewed", "X-Local-Token": "secret"},
            )


class ConnectedCapabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = _config()
        self.message = parse_connected_message(_message()["message"])
        self.claims = {
            "typ": "atellagent_connected_runtime_capability",
            "schema_version": "v1",
            "iss": "gateway",
            "sub": self.message.message_id,
            "aud": [
                "atellagent-connected-runtime",
                f"service-account:{self.config.service_account_id}",
            ],
            "tenant_id": self.config.tenant_id,
            "target_service_account_id": self.config.service_account_id,
            "target_integration_id": self.config.integration_id,
            "target_certificate_public_key_sha256": "certificate-fingerprint",
            "integration_type": "agent",
            "operation": self.message.operation,
            "message_id": self.message.message_id,
            "lease_id": self.message.lease.lease_id,
            "delivery_attempt": self.message.lease.attempt_number,
            "idempotency_key": self.message.idempotency_key,
            "execution_id": self.message.execution_id,
            "execution_attempt_id": self.message.execution_attempt_id,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }

    def _validator(self) -> ConnectedCapabilityValidator:
        validator = object.__new__(ConnectedCapabilityValidator)
        validator._config = self.config
        validator._certificate_public_key_sha256 = "certificate-fingerprint"
        return validator

    async def test_unknown_kid_forces_one_rotation_refresh(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
        jwk["kid"] = "rotated-key"
        token = jwt.encode(
            self.claims,
            private_key,
            algorithm="RS256",
            headers={"kid": "rotated-key"},
        )

        class _Fetcher:
            def __init__(self):
                self.calls = []

            async def get(self, _url, *, force_refresh=False):
                self.calls.append(force_refresh)
                return {"keys": [jwk]} if force_refresh else {"keys": []}

        validator = self._validator()
        validator._fetcher = _Fetcher()
        await validator.validate_token(self.message, token)
        self.assertEqual(validator._fetcher.calls, [False, True])

    async def test_every_delivery_binding_mismatch_fails_closed(self) -> None:
        mismatches = {
            "typ": "wrong",
            "schema_version": "v2",
            "sub": str(uuid4()),
            "tenant_id": str(uuid4()),
            "target_service_account_id": str(uuid4()),
            "target_integration_id": str(uuid4()),
            "target_certificate_public_key_sha256": "wrong",
            "integration_type": "model",
            "operation": "agent.other",
            "message_id": str(uuid4()),
            "lease_id": str(uuid4()),
            "delivery_attempt": 2,
            "idempotency_key": "different-effect",
            "execution_id": "different-execution",
            "execution_attempt_id": "different-attempt",
        }
        for claim, bad_value in mismatches.items():
            with self.subTest(claim=claim):
                validator = self._validator()
                changed = dict(self.claims)
                changed[claim] = bad_value
                validator._decode = AsyncMock(return_value=changed)
                with self.assertRaisesRegex(
                    ConnectedProtocolError, "binding mismatch"
                ):
                    await validator.validate_token(self.message, "token")


if __name__ == "__main__":
    unittest.main()
