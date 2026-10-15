# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Independent device CA publication, fetched before processing issued results."""

from dataclasses import dataclass

import httpx
from cryptography import x509

from .bootstrap_origin import bootstrap_origin, device_api_origin
from .errors import OnboardingClientError
from .http_response import request_json
from .public_ca_bundle import validate_public_ca_bundle


@dataclass(frozen=True)
class DeviceTrustBundle:
    platform_origin: str
    ca_bundle_pem: str
    ca_bundle_sha256: str
    device_api_origin: str

    @classmethod
    def from_payload(cls, payload: dict, *, expected_origin: str) -> "DeviceTrustBundle":
        try:
            origin = bootstrap_origin(expected_origin)
            if (set(payload) != {"platform_origin", "device_api_origin", "protocol_revision", "certificate_profile", "ca_bundle_pem", "ca_bundle_sha256"}
                or payload["platform_origin"] != origin or type(payload["protocol_revision"]) is not int
                or payload["protocol_revision"] != 1 or payload["certificate_profile"] != "atellagent-device-client-v1"):
                raise ValueError("closed device trust publication required")
            api = device_api_origin(payload["device_api_origin"])
            pem = payload["ca_bundle_pem"]
            validate_public_ca_bundle(pem, payload["ca_bundle_sha256"])
            return cls(origin, pem, payload["ca_bundle_sha256"], api)
        except (ValueError, TypeError, KeyError, UnicodeError, x509.ExtensionNotFound, x509.DuplicateExtension) as exc:
            raise OnboardingClientError("onboarding_device_trust_invalid") from exc


async def fetch_device_trust(origin: str) -> DeviceTrustBundle:
    trusted = bootstrap_origin(origin)
    async with httpx.AsyncClient(http2=True, trust_env=False, follow_redirects=False, timeout=15) as client:
        payload = await request_json(client, method="GET", url=trusted + "/.well-known/atellagent-device-trust",
            headers={}, error_prefix="onboarding_device_trust")
    return DeviceTrustBundle.from_payload(payload, expected_origin=trusted)
