# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

from __future__ import annotations

import asyncio
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4


from atellagent_client.connected import (
    ConnectedBridge,
    ConnectedHandlerResult,
    ConnectedParticipant,
    ConnectedProtocolError,
    mount_agent_handler,
)
from atellagent_client.connected.contracts import parse_connected_message
from atellagent_client.sdk import ConnectedSDKRuntime
from atellagent_client.onboarding.device_runtime import DeviceRuntime


from connected_runtime_support import _Response, _Session, _config, _message


class ConnectedRuntimeTests(unittest.IsolatedAsyncioTestCase):
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
                        "atellagent_client.connected.participant_health.asyncio.wait_for",
                        side_effect=complete_one_heartbeat,
                    ),
                    patch(
                        "atellagent_client.connected.participant_health.Path.read_bytes",
                        return_value=b"certificate",
                    ),
                    patch(
                        "atellagent_client.connected.participant_health.x509.load_pem_x509_certificate",
                        return_value=SimpleNamespace(
                            not_valid_after_utc=(
                                datetime.now(timezone.utc) + timedelta(days=30)
                            )
                        ),
                    ),
                    patch(
                        "atellagent_client.connected.participant_health.certificate_public_key_sha256",
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
                self.assertNotIn("native_hook_coverage_health", heartbeat_json)
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

    async def test_stalled_discovery_report_does_not_delay_native_heartbeats(self) -> None:
        config = _config()
        with patch("atellagent_client.connected.participant.ConnectedCapabilityValidator"):
            participant = ConnectedParticipant(config, session=SimpleNamespace())
        instance_id = str(uuid4())
        participant._instance_id = instance_id
        participant.heartbeat_interval = 0.01
        reporter_started = asyncio.Event()
        never_completes = asyncio.Event()
        report_calls = 0
        heartbeat_calls = 0

        async def report() -> None:
            nonlocal report_calls
            report_calls += 1
            reporter_started.set()
            await never_completes.wait()

        async def request(_method, _path, **_kwargs):
            nonlocal heartbeat_calls
            heartbeat_calls += 1
            if heartbeat_calls == 2:
                participant._stop_event.set()
            return _Response(200, {
                "instance_id": instance_id,
                "presence_status": "online",
                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
            })

        runtime = DeviceRuntime(SimpleNamespace(aclose=AsyncMock()),
            SimpleNamespace(run=lambda stop: report()),
            SimpleNamespace(run=lambda stop: stop.wait()), nullcontext())
        with (
            patch.object(participant, "_ensure_registered", new_callable=AsyncMock),
            patch.object(participant, "_request", side_effect=request),
            patch(
                "atellagent_client.connected.participant_health.Path.read_bytes",
                return_value=b"certificate",
            ),
            patch(
                "atellagent_client.connected.participant_health.x509.load_pem_x509_certificate",
                return_value=SimpleNamespace(
                    not_valid_after_utc=datetime.now(timezone.utc) + timedelta(days=30)
                ),
            ),
            patch(
                "atellagent_client.connected.participant_health.certificate_public_key_sha256",
                return_value="a" * 64,
            ),
        ):
            async with runtime:
                await asyncio.wait_for(reporter_started.wait(), timeout=1)
                await asyncio.wait_for(participant._heartbeat_loop(), timeout=1)
                self.assertFalse(runtime.task.done())

        self.assertTrue(reporter_started.is_set())
        self.assertEqual(2, heartbeat_calls)
        self.assertEqual(1, report_calls)
        await participant.stop()
        self.assertTrue(runtime.task.done())

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
