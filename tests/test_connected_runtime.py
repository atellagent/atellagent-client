# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

from __future__ import annotations

import unittest
import httpx
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4


from atellagent_client.connected import (
    ConnectedBridge,
    ConnectedDelivery,
    ConnectedHandlerResult,
    ConnectedOperationHandler,
    ConnectedParticipant,
    mount_filter_handler,
    mount_mcp_handler,
)
from atellagent_client.connected.contracts import parse_connected_message
from atellagent_client.sdk import ConnectedSDKRuntime


from connected_runtime_support import _Response, _config, _message


class ConnectedRuntimeTests(unittest.IsolatedAsyncioTestCase):
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

    def test_native_hook_receipts_preserve_all_claude_code_checkpoints(self) -> None:
        with patch(
            "atellagent_client.connected.participant.ConnectedCapabilityValidator"
        ):
            participant = ConnectedParticipant(_config(), session=SimpleNamespace())

        participant.set_native_hook_event_receipts(
            event_receipts=[
                {"event_name": event_name, "outcome": "completed"}
                for event_name in (
                    "UserPromptSubmit",
                    "PreToolUse",
                    "PostToolUse",
                    "PostToolUseFailure",
                )
            ]
        )

        self.assertEqual(
            [receipt["event_name"] for receipt in participant._native_hook_event_receipts],
            [
                "UserPromptSubmit",
                "PreToolUse",
                "PostToolUse",
                "PostToolUseFailure",
            ],
        )

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
            _mcp_handler=None,
            _mcp_manifest={"tools": [{"name": "file.write"}]},
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

    async def test_filter_mount_requires_and_preserves_the_explicit_boundary(self) -> None:
        participant = SimpleNamespace(register_handler=Mock())

        class FilterHandler:
            request = None

            async def evaluate_filter(self, request):
                self.request = request
                return {"allowed": True, "coverage": "complete", "score": 0.0}

        handler = FilterHandler()
        mount_filter_handler(participant, handler, target_idempotent=True)
        mounted = participant.register_handler.call_args.args[1]
        delivery = ConnectedDelivery(
            message_id=str(uuid4()),
            kind="action",
            operation="filter.evaluate",
            execution_id=None,
            execution_attempt_id=None,
            idempotency_key="filter-effect-1",
            payload_schema="atellagent.connected.ml-filter-evaluation.v1",
            payload={
                "input": {
                    "filter_id": "prompt_injection",
                    "execution_boundary": "model_boundary",
                    "content": "synthetic filter fixture",
                }
            },
            delivery_attempt=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )

        result = await mounted(delivery, SimpleNamespace())

        self.assertEqual(handler.request.execution_boundary, "model_boundary")
        self.assertEqual(result.result_payload["score"], 0.0)

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
        await self._exercise_supervised_rotation()

    async def test_lost_activation_reply_recovers_without_replaying_activation(self) -> None:
        await self._exercise_supervised_rotation(lost_response=True)

    async def test_rotation_outage_retains_delivery_but_permanent_error_is_terminal(self) -> None:
        import asyncio

        for failure, terminal in (
            (httpx.ReadError("temporary issuer outage"), False),
            (ValueError("invalid rotation material"), True),
        ):
            with self.subTest(terminal=terminal), patch(
                "atellagent_client.connected.participant.ConnectedCapabilityValidator"
            ):
                participant = ConnectedParticipant(_config(), session=SimpleNamespace())
                participant._instance_id = str(uuid4())
                participant._perform_certificate_rotation = AsyncMock(side_effect=failure)
                participant._register = AsyncMock()
                participant._commit_result = AsyncMock()
                lease_stopped = asyncio.Event()

                async def renew(*_args):
                    try:
                        await asyncio.Event().wait()
                    finally:
                        lease_stopped.set()

                participant._renew_lease = renew
                participant._receive_enabled.clear()
                # Let renewal actually start before injecting the failure.
                async def fail(_message):
                    await asyncio.sleep(0)
                    raise failure

                participant._perform_certificate_rotation.side_effect = fail
                await participant._process_certificate_rotation(
                    parse_connected_message(_message()["message"])
                )
                self.assertTrue(lease_stopped.is_set())
                self.assertTrue(participant._receive_enabled.is_set())
                participant._register.assert_awaited_once()
                if terminal:
                    participant._commit_result.assert_awaited_once()
                    self.assertEqual(
                        participant._commit_result.await_args.args[1].terminal_status,
                        "failed",
                    )
                else:
                    participant._commit_result.assert_not_awaited()

    async def _exercise_supervised_rotation(self, *, lost_response=False) -> None:
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
            public_key_sha256="b" * 64,
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
        recovered_payload = responses[2].json()
        if lost_response:
            responses[2] = httpx.ReadError("activation acknowledgement lost")
        with (
            patch(
                "atellagent_client.connected.participant.ConnectedCapabilityValidator"
            ),
            patch(
                "atellagent_client.connected.participant_rotation.certificate_public_key_sha256",
                return_value="a" * 64,
            ),
            patch(
                "atellagent_client.connected.participant_rotation.prepare_certificate_rotation",
                return_value=prepared,
            ),
            patch(
                "atellagent_client.connected.participant_rotation.stage_certificate_rotation",
                return_value=staged,
            ) as stage,
            patch(
                "atellagent_client.connected.participant_rotation.fetch_service_account_trust",
                new=AsyncMock(return_value=SimpleNamespace(ca_bundle_pem="independent trust")),
            ) as trust,
            patch(
                "atellagent_client.connected.participant_rotation.recover_activated_rotation",
                new=AsyncMock(return_value=recovered_payload),
            ) as recover,
        ):
            participant = ConnectedParticipant(_config(), session=SimpleNamespace())
            participant._instance_id = str(uuid4())
            participant._wait_for_other_deliveries = AsyncMock()
            participant._drain_for_certificate_rotation = AsyncMock()
            participant._rotation_request = AsyncMock(side_effect=responses)
            participant._install_activated_rotation = AsyncMock()
            result = await participant._perform_certificate_rotation(message)

        self.assertEqual(result.result_payload["status"], "reconnected")
        self.assertEqual(recover.await_count, 1 if lost_response else 0)
        trust.assert_awaited_once_with(participant.config.platform_origin)
        self.assertEqual(stage.call_args.kwargs["trusted_ca_pem"], "independent trust")
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
