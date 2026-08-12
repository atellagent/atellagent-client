# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Verification of one HSM-signed connected delivery capability."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict

from cryptography import x509
from cryptography.hazmat.primitives import serialization
import jwt

from atellagent_client.protocol.api import build_versioned_route, strip_api_suffix
from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.gateway.session import GatewaySession
from atellagent_client.sdk.jwks import JWKSFetcher, get_jwk_by_kid

from .contracts import ConnectedMessage, ConnectedProtocolError


CONNECTED_CAPABILITY_AUDIENCE = "atellagent-connected-runtime"


def certificate_public_key_sha256(cert_path: str) -> str:
    try:
        certificate = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    except Exception as exc:
        raise ConnectedProtocolError("unable to load enrolled client certificate") from exc
    public_key_der = certificate.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(public_key_der).hexdigest()


class ConnectedCapabilityValidator:
    def __init__(self, config: ServiceAccountConfig, session: GatewaySession) -> None:
        self._config = config
        self._fetcher = JWKSFetcher(session.http_client_manager, ttl_seconds=300)
        self._certificate_public_key_sha256 = certificate_public_key_sha256(
            str(config.cert_path)
        )

    def _jwks_url(self) -> str:
        base = strip_api_suffix(self._config.gateway_url).rstrip("/")
        return f"{base}{build_versioned_route(self._config.api_version, '/jwks/execution-tokens')}"

    async def _decode(self, token: str) -> Dict[str, Any]:
        url = self._jwks_url()
        try:
            header = jwt.get_unverified_header(token)
            kid = str(header.get("kid") or "").strip()
        except jwt.PyJWTError as exc:
            raise ConnectedProtocolError("connected capability header is invalid") from exc
        if not kid:
            raise ConnectedProtocolError("connected capability has no key id")
        jwks = await self._fetcher.get(url)
        jwk = get_jwk_by_kid(jwks, kid)
        if jwk is None:
            jwks = await self._fetcher.get(url, force_refresh=True)
            jwk = get_jwk_by_kid(jwks, kid)
        if jwk is None:
            raise ConnectedProtocolError("connected capability key is unavailable")
        try:
            key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=CONNECTED_CAPABILITY_AUDIENCE,
                issuer=os.getenv("ATELLAGENT_EXECUTION_TOKEN_ISS", "gateway"),
                options={"verify_exp": True, "verify_aud": True, "verify_iss": True},
            )
        except jwt.ExpiredSignatureError as exc:
            raise ConnectedProtocolError("connected capability expired") from exc
        except jwt.PyJWTError as exc:
            raise ConnectedProtocolError("connected capability is invalid") from exc
        return dict(claims)

    async def validate_token(self, message: ConnectedMessage, token: str) -> None:
        claims = await self._decode(token)
        audiences = claims.get("aud")
        audience_set = {audiences} if isinstance(audiences, str) else set(audiences or [])
        required_audiences = {
            CONNECTED_CAPABILITY_AUDIENCE,
            f"service-account:{self._config.service_account_id}",
        }
        if not required_audiences.issubset(audience_set):
            raise ConnectedProtocolError("connected capability audience binding mismatch")
        expected = {
            "typ": "atellagent_connected_runtime_capability",
            "schema_version": "v1",
            "sub": message.message_id,
            "tenant_id": str(self._config.tenant_id),
            "target_service_account_id": str(self._config.service_account_id),
            "target_integration_id": str(self._config.integration_id),
            "target_certificate_public_key_sha256": self._certificate_public_key_sha256,
            "integration_type": str(self._config.integration_type),
            "operation": message.operation,
            "message_id": message.message_id,
            "lease_id": message.lease.lease_id,
            "delivery_attempt": message.lease.attempt_number,
            "idempotency_key": message.idempotency_key,
            "execution_id": message.execution_id,
            "execution_attempt_id": message.execution_attempt_id,
        }
        for key, value in expected.items():
            if claims.get(key) != value:
                raise ConnectedProtocolError(
                    f"connected capability binding mismatch for {key}"
                )

    async def validate(self, message: ConnectedMessage) -> None:
        await self.validate_token(message, message.capability)


__all__ = [
    "CONNECTED_CAPABILITY_AUDIENCE",
    "ConnectedCapabilityValidator",
    "certificate_public_key_sha256",
]
