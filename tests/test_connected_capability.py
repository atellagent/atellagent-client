# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from atellagent_client.connected.capability import ConnectedCapabilityValidator
from atellagent_client.connected.contracts import ConnectedProtocolError, parse_connected_message
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
        capabilities=["agent.invoke"],
        cert_path="/tmp/client.crt",
        key_path="/tmp/client.key",
        integration_type="agent",
        identity_mode="boundary_identity_only",
        deployment=SDKDeploymentConfig(),
    )


def _message() -> dict[str, object]:
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
                with self.assertRaisesRegex(ConnectedProtocolError, "binding mismatch"):
                    await validator.validate_token(self.message, "token")


if __name__ == "__main__":
    unittest.main()
