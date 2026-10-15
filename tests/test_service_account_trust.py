"""Independent service-account trust uses HTTPS, not returned leaf material."""

import httpx
import pytest

from atellagent_client.onboarding import service_account_trust as module
from atellagent_client.onboarding.errors import OnboardingClientError
from device_certificate_support import certificate_case


@pytest.fixture
def publication():
    _, _, trust, _ = certificate_case()
    return {"platform_origin": trust.platform_origin, "service_account_api_origin": trust.device_api_origin,
        "protocol_revision": 1, "certificate_profile": "atellagent-service-account-client-v1",
        "ca_bundle_pem": trust.ca_bundle_pem, "ca_bundle_sha256": trust.ca_bundle_sha256}


@pytest.mark.asyncio
async def test_fetch_uses_fixed_origin_with_no_credentials_redirect_or_environment_proxy(publication, monkeypatch):
    factory = httpx.AsyncClient
    def handler(request):
        assert str(request.url) == publication["platform_origin"] + module.SERVICE_ACCOUNT_TRUST_PATH
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        return httpx.Response(200, json=publication)
    def create(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return factory(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(module.httpx, "AsyncClient", create)
    trust = await module.fetch_service_account_trust(publication["platform_origin"])
    assert trust.ca_bundle_sha256 == publication["ca_bundle_sha256"]


@pytest.mark.parametrize("change", [{"protocol_revision": True},
    {"certificate_profile": "atellagent-device-client-v1"}, {"platform_origin": "https://foreign.example"},
    {"service_account_api_origin": "https://localhost:8443"}, {"ca_bundle_sha256": "0" * 64},
    {"ca_bundle_pem": "PRIVATE KEY"}, {"secret": "not-public"}])
def test_foreign_purpose_or_malformed_publication_rejected(publication, change):
    with pytest.raises(OnboardingClientError, match="service_account_trust_invalid"):
        module.ServiceAccountTrustBundle.from_payload({**publication, **change},
            expected_origin=publication["platform_origin"])
