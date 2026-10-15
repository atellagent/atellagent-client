# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Closed bootstrap metadata, not a configurable issuer or enrollment capability."""

from dataclasses import dataclass
import re

from .bootstrap_origin import bootstrap_origin
from .errors import OnboardingClientError

MANAGEMENT_SCOPES = ("openid", "profile", "email", "client.onboarding", "client.manage")
_REALM = re.compile(r"^/auth/realms/[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True)
class BootstrapMetadata:
    platform_origin: str
    issuer_url: str
    device_authorization_url: str
    token_url: str
    revocation_url: str
    browser_authorization_url: str
    client_id: str = "atellagent-client"
    audience: str = "atellagent-client"
    scopes: tuple[str, ...] = MANAGEMENT_SCOPES
    protocol_revision: int = 1

    @classmethod
    def from_payload(cls, payload: dict, *, expected_origin: str) -> "BootstrapMetadata":
        origin = bootstrap_origin(expected_origin)
        fields = set(cls.__dataclass_fields__)
        if (not isinstance(payload, dict) or set(payload) != fields
            or type(payload.get("protocol_revision")) is not int or payload["protocol_revision"] < 1
            or payload.get("platform_origin") != origin
            or payload.get("client_id") != "atellagent-client"
            or payload.get("audience") != "atellagent-client"
            or payload.get("scopes") != list(MANAGEMENT_SCOPES)):
            raise OnboardingClientError("onboarding_bootstrap_invalid")
        issuer = payload.get("issuer_url")
        if not isinstance(issuer, str) or not issuer.startswith(origin) or not _REALM.fullmatch(issuer[len(origin):]):
            raise OnboardingClientError("onboarding_bootstrap_invalid")
        endpoints = {"device_authorization_url": issuer + "/protocol/openid-connect/auth/device",
            "token_url": issuer + "/protocol/openid-connect/token",
            "revocation_url": issuer + "/protocol/openid-connect/revoke",
            "browser_authorization_url": origin + "/app/device"}
        if any(payload.get(name) != value for name, value in endpoints.items()):
            raise OnboardingClientError("onboarding_bootstrap_invalid")
        if payload["protocol_revision"] != 1:
            raise OnboardingClientError("onboarding_upgrade_required")
        return cls(**{**payload, "scopes": MANAGEMENT_SCOPES})
