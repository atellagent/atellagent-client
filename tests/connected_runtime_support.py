# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4


from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.config_models import (
    BridgeDeploymentConfig,
    SDKDeploymentConfig,
)


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
        platform_origin="https://platform.example",
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
        identity_mode=(
            "boundary_identity_only" if integration_type in {"agent", "mcp"} else None
        ),
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

