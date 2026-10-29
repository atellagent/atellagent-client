# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Verification and short-lived caching for native-hook outage posture."""

from __future__ import annotations

import os
from dataclasses import dataclass
from time import time
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

import jwt

from atellagent_client.protocol.api import build_versioned_route, strip_api_suffix
from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.http import HTTPClientManager
from atellagent_client.sdk.jwks import JWKSFetcher, get_jwk_by_kid


_POSTURE_AUDIENCE = "atellagent-client-native-hook-posture"
_POSTURE_VERSION = "v1"


@dataclass(frozen=True)
class NativeHookPosture:
    enforcement_mode: str
    coverage_posture: str
    native_host: str
    host_adapter_version: str
    revision: int
    expires_at: int

    def permits_observe_outage(self, *, host: str, adapter_version: str) -> bool:
        return (
            self.enforcement_mode == "observe"
            and self.expires_at > int(time())
            and self.native_host == str(host or "").strip()
            and self.host_adapter_version == str(adapter_version or "").strip()
        )


class NativeHookPostureCache:
    """A verified in-memory cache; static config is never posture authority."""

    def __init__(self, config: ServiceAccountConfig) -> None:
        self._config = config
        self._http: Optional[HTTPClientManager] = None
        self._jwks: Optional[JWKSFetcher] = None
        self._posture: Optional[NativeHookPosture] = None

    @property
    def posture(self) -> Optional[NativeHookPosture]:
        return self._posture

    def diagnostics(self) -> dict[str, Any]:
        posture = self._posture
        return {
            "status": (
                "valid" if posture and posture.expires_at > int(time()) else "missing_or_expired"
            ),
            "enforcement_mode": posture.enforcement_mode if posture else None,
            "coverage_posture": posture.coverage_posture if posture else None,
            "revision": posture.revision if posture else None,
            "expires_at": posture.expires_at if posture else None,
        }

    def _jwks_url(self) -> str:
        base = urlparse(strip_api_suffix(self._config.gateway_url)).geturl().rstrip("/")
        return f"{base}{build_versioned_route(self._config.api_version, '/jwks/execution-tokens')}"

    async def refresh(self, encoded_posture: str) -> NativeHookPosture:
        try:
            header = jwt.get_unverified_header(encoded_posture)
            key_id = str(header.get("kid") or "").strip()
            if not key_id:
                raise ValueError("missing key id")
            jwks = await self._jwks_fetcher().get(self._jwks_url())
            jwk = get_jwk_by_kid(jwks, key_id)
            if not jwk:
                raise ValueError("unknown key id")
            claims = jwt.decode(
                encoded_posture,
                jwt.algorithms.RSAAlgorithm.from_jwk(jwk),
                algorithms=["RS256"],
                audience=_POSTURE_AUDIENCE,
                issuer=os.getenv("ATELLAGENT_CONTROL_DIRECTIVE_ISS", "gateway"),
                options={"require": ["exp", "aud", "iss", "jti"]},
            )
            posture = self._from_claims(claims)
        except Exception as exc:
            raise ValueError("native_hook_posture_invalid") from exc
        self._posture = posture
        return posture

    def _jwks_fetcher(self) -> JWKSFetcher:
        if self._jwks is None:
            cert = (
                (self._config.cert_path, self._config.key_path)
                if self._config.cert_path and self._config.key_path
                else None
            )
            self._http = HTTPClientManager(timeout=self._config.timeout, cert=cert)
            self._jwks = JWKSFetcher(self._http)
        return self._jwks

    def _from_claims(self, claims: Mapping[str, Any]) -> NativeHookPosture:
        if (
            claims.get("typ") != "atellagent_native_hook_posture"
            or claims.get("schema_version") != _POSTURE_VERSION
            or str(claims.get("tenant_id") or "") != str(self._config.tenant_id or "")
            or str(claims.get("service_account_id") or "")
            != str(self._config.service_account_id or "")
            or str(claims.get("integration_id") or "")
            != str(self._config.integration_id or "")
        ):
            raise ValueError("posture identity is invalid")
        mode = str(claims.get("enforcement_mode") or "")
        coverage = str(claims.get("coverage_posture") or "")
        host = str(claims.get("native_host") or "").strip()
        adapter = str(claims.get("host_adapter_version") or "").strip()
        try:
            revision = int(claims.get("revision"))
            expires_at = int(claims.get("exp"))
        except (TypeError, ValueError) as exc:
            raise ValueError("posture fields are invalid") from exc
        if (
            mode not in {"observe", "enforce"}
            or coverage not in {"permissive", "strict"}
            or not host
            or not adapter
            or revision < 1
            or expires_at <= int(time())
        ):
            raise ValueError("posture fields are invalid")
        return NativeHookPosture(
            enforcement_mode=mode,
            coverage_posture=coverage,
            native_host=host,
            host_adapter_version=adapter,
            revision=revision,
            expires_at=expires_at,
        )


__all__ = ["NativeHookPosture", "NativeHookPostureCache"]
