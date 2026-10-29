# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Closed machine endpoint metadata shared by authenticated device/workload reads."""

from dataclasses import dataclass
import re
from urllib.parse import urlsplit
from .bootstrap_origin import bootstrap_origin, device_api_origin
from .errors import OnboardingClientError

RUNTIME_METADATA_PATH = "/devices/self/runtime-metadata"
_REALM = re.compile(r"^/auth/realms/[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True)
class RuntimeMetadata:
    platform_origin: str
    gateway_origin: str
    issuer_url: str
    token_url: str
    jwks_url: str

    @classmethod
    def from_payload(cls, payload, *, expected_origin, expected_gateway_origin):
        try:
            if not isinstance(payload, dict) or set(payload) != set(cls.__dataclass_fields__):
                raise ValueError("closed machine endpoint metadata required")
            if (payload["platform_origin"] != bootstrap_origin(expected_origin)
                    or payload["gateway_origin"] != device_api_origin(expected_gateway_origin)):
                raise ValueError("exact deployment and gateway required")
            issuer = urlsplit(payload["issuer_url"])
            origin = device_api_origin(f"{issuer.scheme}://{issuer.netloc}")
            if (not _REALM.fullmatch(issuer.path) or payload["issuer_url"] != origin + issuer.path
                    or payload["token_url"] != payload["issuer_url"] + "/protocol/openid-connect/token"
                    or payload["jwks_url"] != payload["issuer_url"] + "/protocol/openid-connect/certs"):
                raise ValueError("exact public machine issuer endpoints required")
            return cls(**payload)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise OnboardingClientError("onboarding_runtime_metadata_invalid") from exc


async def fetch_runtime_metadata(client, *, platform_origin, gateway_origin):
    payload = await client._request("GET", RUNTIME_METADATA_PATH)
    return RuntimeMetadata.from_payload(payload, expected_origin=platform_origin,
                                        expected_gateway_origin=gateway_origin)
