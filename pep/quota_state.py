# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under the terms in LICENSE.md.
"""Verified, bounded quota disposition for customer-side execution.

A quota instruction never replaces an action directive or authenticates an MCP
caller. Callers retain their local restrictions and transport/access controls.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime
import os
import random
import threading
from time import monotonic, time
from typing import Any, Callable, Mapping

import jwt

from atellagent_client.protocol.api import build_versioned_route, strip_api_suffix
from atellagent_client.sdk.http import HTTPClientManager
from atellagent_client.sdk.jwks import JWKSFetcher, get_jwk_by_kid

AUDIENCE = "atellagent-client-quota-state"
TOKEN_TYPE = "atellagent_quota_state"
QUOTA_CODE = "governed_action_decision_quota_exhausted"
SURFACES = frozenset({"native_hook", "local_model", "mcp_pep"})
MAX_AUTHORITY_SECONDS = 60


class QuotaStateUnavailable(ValueError):
    """Expired/missing authority cannot authorize local continuation."""


@dataclass(frozen=True)
class QuotaDisposition:
    state: str
    disposition: str
    expires_at: int
    entitlement_revision: int
    settings_revision: int
    authorization_id: str | None
    authorization_epoch: int | None
    connection_revision: int = 1
    issued_at: int = 0

    @property
    def continues_locally(self):
        return self.state == "exhausted" and self.disposition in {"passthrough", "observe"} and self.expires_at > time()


def quota_error_detail(status: int, payload: Any) -> dict | None:
    """Only the dedicated quota envelope can trigger instruction processing."""
    if status != 429 or not isinstance(payload, dict):
        return None
    detail = payload
    for _ in range(3):
        if "violation_type" in detail:
            return None
        if detail.get("error") == "entitlement_denied" and detail.get("code") == QUOTA_CODE:
            return detail
        nested = detail.get("detail", detail.get("error"))
        if not isinstance(nested, dict):
            return None
        detail = nested
    return None


class QuotaStateCache:
    """One fixed connection/surface observation, with single-flight recovery."""

    def __init__(self, config, *, surface: str, fetch: Callable,
                 authorization_binding: Callable[[], Mapping] | None = None, http_client_manager=None, expected_surfaces=None):
        if surface not in SURFACES:
            raise ValueError("unsupported local quota surface")
        self.config, self.surface, self.fetch = config, surface, fetch
        self.expected_surfaces = frozenset(expected_surfaces or {surface})
        if not self.expected_surfaces.issubset(SURFACES) or surface not in self.expected_surfaces:
            raise ValueError("unsupported quota scope")
        self.authorization_binding = authorization_binding
        self._state: QuotaDisposition | None = None
        self._http = self._jwks = None
        self._provided_http = http_client_manager
        self._pending: Future | None = None
        self._lock = threading.Lock()
        self._retry_at = 0.0
        self._backoff = 2.0
        self._suspended = False

    def _binding(self):
        value = self.authorization_binding() if self.authorization_binding is not None else {}
        return (str(value.get("authorization_id") or "") or None, value.get("authorization_epoch"))

    def current(self) -> QuotaDisposition | None:
        value = self._state
        if value is None or value.expires_at <= time():
            return None
        if (value.authorization_id, value.authorization_epoch) != self._binding():
            self._state = None
            return None
        return value

    def invalidate(self):
        self._state = None
        self._suspended = True

    def diagnostics(self):
        value = self.current()
        return {"state": value.state if value else "expired" if self._suspended else "unknown",
                "disposition": value.disposition if value else None,
                "expires_at": value.expires_at if value else None}

    async def before_action(self) -> QuotaDisposition | None:
        value = self.current()
        if value is not None:
            return value if value.state == "exhausted" else None
        if not self._suspended:
            return None
        value = await self.recover()
        return value if value.state == "exhausted" else None

    async def accept_error(self, status: int, payload: Any) -> QuotaDisposition | None:
        detail = quota_error_detail(status, payload)
        if detail is None:
            return None
        self._suspended = True
        encoded = detail.get("quota_state_instruction")
        if isinstance(encoded, str) and encoded:
            return await self.accept_instruction(encoded)
        return await self.recover()

    async def accept_instruction(self, encoded: str) -> QuotaDisposition:
        try:
            if not isinstance(encoded, str) or len(encoded) > 16384:
                raise ValueError("invalid quota instruction")
            header = jwt.get_unverified_header(encoded)
            kid = header.get("kid")
            if not isinstance(kid, str) or not kid or header.get("alg") != "RS256":
                raise ValueError("invalid quota key")
            if self._jwks is None:
                cert = ((self.config.cert_path, self.config.key_path)
                        if self.config.cert_path and self.config.key_path else None)
                self._http = self._provided_http or HTTPClientManager(timeout=self.config.timeout, cert=cert)
                self._jwks = JWKSFetcher(self._http)
            url = strip_api_suffix(self.config.gateway_url).rstrip('/') + build_versioned_route(self.config.api_version, '/jwks/execution-tokens')
            jwk = get_jwk_by_kid(await self._jwks.get(url), kid)
            if not jwk:
                raise ValueError("unknown quota key")
            claims = jwt.decode(encoded, jwt.algorithms.RSAAlgorithm.from_jwk(jwk), algorithms=["RS256"],
                audience=AUDIENCE, issuer=os.getenv("ATELLAGENT_CONTROL_DIRECTIVE_ISS", "gateway"),
                options={"require": ["exp", "iat", "aud", "iss", "jti"]})
            value = self._from_claims(claims)
        except Exception as exc:
            self._state = None
            self._suspended = True
            raise QuotaStateUnavailable("quota_state_instruction_invalid") from exc
        previous = self._state
        if previous is not None and (
                value.settings_revision < previous.settings_revision
                or value.entitlement_revision < previous.entitlement_revision
                or value.connection_revision < previous.connection_revision
                or value.issued_at < previous.issued_at):
            return previous
        self._state = value
        self._suspended = value.state == "exhausted"
        self._backoff = 2.0
        self._retry_at = 0
        return value

    def _from_claims(self, claims):
        if (claims.get("typ") != TOKEN_TYPE or claims.get("schema_version") != "v1"
                or claims.get("tenant_id") != str(self.config.tenant_id or "")
                or claims.get("integration_id") != str(self.config.integration_id or "")
                or claims.get("sub") != str(self.config.integration_id or "")
                or claims.get("service_account_id") != str(self.config.service_account_id or "")
                or not all(str(value or "").strip() for value in (self.config.tenant_id, self.config.integration_id, self.config.service_account_id))
                or claims.get("surface") not in self.expected_surfaces
                or not isinstance(claims.get('surfaces'), list)
                or not all(isinstance(value, str) for value in claims['surfaces'])
                or not self.expected_surfaces.issubset(set(claims['surfaces']))
                or not set(claims['surfaces']).issubset(SURFACES)
                or claims.get("capability_code") != "governed_action_decision"
                or claims.get("state") not in {"available", "exhausted"}
                or claims.get("disposition") not in {"stop", "passthrough", "observe"}
                or any(type(claims.get(key)) is not int for key in ("exp", "iat", "entitlement_revision", "settings_revision", "connection_revision"))
                or min(claims["entitlement_revision"], claims["settings_revision"], claims["connection_revision"]) < 1
                or not claims["iat"] <= int(time()) < claims["exp"]
                or not 0 < claims["exp"] - claims["iat"] <= MAX_AUTHORITY_SECONDS):
            raise ValueError("quota scope invalid")
        start, end = (datetime.fromisoformat(claims[key]) for key in ("period_start", "period_end"))
        if start.tzinfo is None or end.tzinfo is None or not start.timestamp() <= time() < end.timestamp() or claims["exp"] > end.timestamp():
            raise ValueError("quota period invalid")
        expected_id, expected_epoch = self._binding()
        if (claims.get("authorization_id"), claims.get("authorization_epoch")) != (expected_id, expected_epoch):
            raise ValueError("quota authorization binding invalid")
        if expected_id is not None and (type(expected_epoch) is not int or expected_epoch < 1):
            raise ValueError("quota authorization epoch invalid")
        return QuotaDisposition(claims["state"], claims["disposition"], claims["exp"],
            claims["entitlement_revision"], claims["settings_revision"], expected_id, expected_epoch,
            claims["connection_revision"], claims["iat"])

    async def recover(self, *, force=False) -> QuotaDisposition:
        with self._lock:
            pending = self._pending
            owner = pending is None
            if owner:
                if not force and monotonic() < self._retry_at:
                    raise QuotaStateUnavailable("quota_recovery_backoff")
                pending = self._pending = Future()
        if owner:
            async def refresh():
                try:
                    async with asyncio.timeout(5):
                        response = await self.fetch(self.surface)
                        if response.status_code != 200:
                            raise QuotaStateUnavailable("quota_recovery_unavailable")
                        payload = response.json()
                        encoded = payload.get("quota_state_instruction") if isinstance(payload, dict) else None
                        if not encoded:
                            raise QuotaStateUnavailable("quota_recovery_instruction_missing")
                        value = await self.accept_instruction(encoded)
                    pending.set_result(value)
                except BaseException as exc:
                    self._state = None
                    self._suspended = True
                    with self._lock:
                        self._retry_at = monotonic() + self._backoff + random.uniform(0, 1)
                        self._backoff = min(30, self._backoff * 2)
                    pending.set_exception(exc)
                finally:
                    with self._lock:
                        if self._pending is pending:
                            self._pending = None
            task = asyncio.create_task(refresh())
            task.add_done_callback(lambda result: None if result.cancelled() else result.exception())
        return await asyncio.shield(asyncio.wrap_future(pending))
