# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from atellagent_client.connected import (
    ConnectedParticipant,
    mount_agent_handler,
    mount_workflow_handler,
)
from atellagent_client.connected.contracts import parse_connected_message
from atellagent_client.sdk.gateway.session import GatewaySession


from connected_runtime_support import _Response, _Client, _config, _message


class _AsyncLease:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_args):
        return None


class _SyncLease:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *_args):
        return None


class ConnectedRuntimeTests(unittest.IsolatedAsyncioTestCase):
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
        session._auth_headers_async = AsyncMock(
            side_effect=[
                {"Authorization": "Bearer stale"},
                {"Authorization": "Bearer fresh"},
            ]
        )
        session.http_client_manager.create_async_client = Mock(
            side_effect=lambda: _AsyncLease(client)
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

    async def test_gateway_connection_error_retries_once_without_resetting_pool(self) -> None:
        first_client = SimpleNamespace(request=AsyncMock(side_effect=httpx.ConnectError("connection failed")))
        second_client = SimpleNamespace(request=AsyncMock(return_value=_Response(204, None)))
        manager = SimpleNamespace(reset_async_client=AsyncMock())
        session = GatewaySession(
            config=_config(),
            auth_manager=SimpleNamespace(invalidate_token=Mock()),
            http_client_manager=manager,
            oauth_http_client_manager=SimpleNamespace(),
            base_url="https://mtls.gateway.example",
        )
        session._auth_headers_async = AsyncMock(
            return_value={"Authorization": "Bearer current"}
        )
        session.http_client_manager.create_async_client = Mock(
            side_effect=[_AsyncLease(first_client), _AsyncLease(second_client)]
        )

        response = await session.request_authenticated(
            "POST", "https://mtls.gateway.example/heartbeat", json={}
        )

        self.assertEqual(response.status_code, 204)
        manager.reset_async_client.assert_not_awaited()
        self.assertEqual(first_client.request.await_count, 1)
        self.assertEqual(second_client.request.await_count, 1)

    def test_sync_request_refreshes_a_rejected_cached_oauth_token_once(self) -> None:
        client = SimpleNamespace(request=Mock(side_effect=[_Response(401, {}), _Response(204, None)]))
        auth = SimpleNamespace(invalidate_token=Mock())
        session = GatewaySession(
            config=_config(),
            auth_manager=auth,
            http_client_manager=SimpleNamespace(),
            oauth_http_client_manager=SimpleNamespace(),
            base_url="https://mtls.gateway.example",
        )
        session._auth_headers_sync = Mock(
            side_effect=[
                {"Authorization": "Bearer stale"},
                {"Authorization": "Bearer fresh"},
            ]
        )
        session.http_client_manager.create_sync_client = Mock(
            side_effect=lambda: _SyncLease(client)
        )

        response = session.request_authenticated_sync(
            "POST", "https://mtls.gateway.example/heartbeat", json={}
        )

        self.assertEqual(response.status_code, 204)
        auth.invalidate_token.assert_called_once_with()
        self.assertEqual(
            [call.kwargs["headers"]["Authorization"] for call in client.request.call_args_list],
            ["Bearer stale", "Bearer fresh"],
        )

    def test_sync_request_retries_one_connection_failure_without_resetting_pool(self) -> None:
        first_client = SimpleNamespace(request=Mock(side_effect=httpx.ConnectError("connection failed")))
        second_client = SimpleNamespace(request=Mock(return_value=_Response(204, None)))
        manager = SimpleNamespace(reset_sync_client=Mock())
        session = GatewaySession(
            config=_config(),
            auth_manager=SimpleNamespace(invalidate_token=Mock()),
            http_client_manager=manager,
            oauth_http_client_manager=SimpleNamespace(),
            base_url="https://mtls.gateway.example",
        )
        session._auth_headers_sync = Mock(
            return_value={"Authorization": "Bearer current"}
        )
        session.http_client_manager.create_sync_client = Mock(
            side_effect=[_SyncLease(first_client), _SyncLease(second_client)]
        )

        response = session.request_authenticated_sync(
            "POST", "https://mtls.gateway.example/heartbeat", json={}
        )

        self.assertEqual(response.status_code, 204)
        manager.reset_sync_client.assert_not_called()
        self.assertEqual(first_client.request.call_count, 1)
        self.assertEqual(second_client.request.call_count, 1)
