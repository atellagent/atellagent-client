# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Gateway-JWKS verification for opaque connected-control directives."""

from __future__ import annotations

import os
from time import time
from typing import Callable, Optional
from urllib.parse import urlparse

import jwt

from atellagent_client.protocol.api import build_versioned_route, strip_api_suffix
from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.http import HTTPClientManager
from atellagent_client.sdk.jwks import JWKSFetcher, get_jwk_by_kid

from .contracts import (
    ActionIntent,
    DirectiveValidationError,
    RemoteControlDirective,
    directive_from_verified_claims,
)

CONTROL_DIRECTIVE_AUDIENCE = "atellagent-client-pep"


class GatewayDirectiveVerifier:
    """Verify a gateway-signed directive using its published JWKS.

    This is cryptographic client plumbing, not a local policy evaluator. A
    missing, invalid, stale, replayed, or differently scoped directive fails
    closed before the adapter invokes its declared local side effect.
    """

    def __init__(
        self,
        config: ServiceAccountConfig,
        *,
        audience: str = CONTROL_DIRECTIVE_AUDIENCE,
        issuer: Optional[str] = None,
        is_revoked: Optional[Callable[[str], bool]] = None,
        now: Callable[[], float] = time,
    ) -> None:
        self._config = config
        self._audience = audience
        self._issuer = issuer or os.getenv("ATELLAGENT_CONTROL_DIRECTIVE_ISS", "gateway")
        self._is_revoked = is_revoked or (lambda _directive_id: False)
        self._now = now
        self._seen: set[str] = set()
        cert = (
            (config.cert_path, config.key_path)
            if config.cert_path and config.key_path
            else None
        )
        self._http = HTTPClientManager(
            timeout=config.timeout,
            cert=cert,
        )
        self._jwks = JWKSFetcher(self._http)

    def _jwks_url(self) -> str:
        base = urlparse(strip_api_suffix(self._config.gateway_url)).geturl().rstrip("/")
        return f"{base}{build_versioned_route(self._config.api_version, '/jwks/execution-tokens')}"

    async def verify(
        self, encoded_directive: str, intent: ActionIntent
    ) -> RemoteControlDirective:
        if not str(encoded_directive or "").strip():
            raise DirectiveValidationError("remote_directive_unavailable")
        try:
            header = jwt.get_unverified_header(encoded_directive)
            key_id = str(header.get("kid") or "").strip()
            if not key_id:
                raise ValueError("missing key id")
            jwks = await self._jwks.get(self._jwks_url())
            jwk = get_jwk_by_kid(jwks, key_id)
            if not jwk:
                raise ValueError("unknown key id")
            key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
            claims = jwt.decode(
                encoded_directive,
                key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "aud", "iss", "jti"]},
            )
        except DirectiveValidationError:
            raise
        except Exception as exc:
            raise DirectiveValidationError("remote_directive_invalid") from exc
        return directive_from_verified_claims(
            claims,
            intent,
            audience=self._audience,
            issuer=self._issuer,
            is_revoked=self._is_revoked,
            seen=self._seen,
            now=self._now,
        )


__all__ = ["CONTROL_DIRECTIVE_AUDIENCE", "GatewayDirectiveVerifier"]
