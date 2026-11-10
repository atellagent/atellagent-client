# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Independent service-account signing anchors from the trusted platform HTTPS origin."""

from dataclasses import dataclass
import httpx
from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm

from .bootstrap_origin import bootstrap_origin, device_api_origin
from .errors import OnboardingClientError
from .http_response import request_json
from .public_ca_bundle import validate_public_ca_bundle

SERVICE_ACCOUNT_TRUST_PATH = "/.well-known/atellagent-service-account-trust"


@dataclass(frozen=True)
class ServiceAccountTrustBundle:
    platform_origin: str
    service_account_api_origin: str
    ca_bundle_pem: str
    ca_bundle_sha256: str

    def validated(self, *, expected_origin):
        return self.from_payload({"platform_origin": self.platform_origin,
            "service_account_api_origin": self.service_account_api_origin, "protocol_revision": 1,
            "certificate_profile": "atellagent-service-account-client-v1",
            "ca_bundle_pem": self.ca_bundle_pem, "ca_bundle_sha256": self.ca_bundle_sha256},
            expected_origin=expected_origin)

    @classmethod
    def from_payload(cls, payload, *, expected_origin):
        try:
            origin = bootstrap_origin(expected_origin)
            if (not isinstance(payload, dict) or set(payload) != {"platform_origin", "service_account_api_origin",
                    "protocol_revision", "certificate_profile", "ca_bundle_pem", "ca_bundle_sha256"}
                    or payload["platform_origin"] != origin or type(payload["protocol_revision"]) is not int
                    or payload["protocol_revision"] != 1
                    or payload["certificate_profile"] != "atellagent-service-account-client-v1"):
                raise ValueError("closed service-account trust publication required")
            api = device_api_origin(payload["service_account_api_origin"])
            validate_public_ca_bundle(payload["ca_bundle_pem"], payload["ca_bundle_sha256"])
            return cls(origin, api, payload["ca_bundle_pem"], payload["ca_bundle_sha256"])
        except (ValueError, TypeError, KeyError, UnicodeError, UnsupportedAlgorithm,
                x509.ExtensionNotFound, x509.DuplicateExtension) as exc:
            raise OnboardingClientError("onboarding_service_account_trust_invalid") from exc


async def fetch_service_account_trust(origin):
    trusted = bootstrap_origin(origin)
    async with httpx.AsyncClient(http2=True, trust_env=False, follow_redirects=False, timeout=15) as client:
        payload = await request_json(client, method="GET", url=trusted + SERVICE_ACCOUNT_TRUST_PATH,
            headers={}, error_prefix="onboarding_service_account_trust")
    return ServiceAccountTrustBundle.from_payload(payload, expected_origin=trusted)
