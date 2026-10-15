"""Public bootstrap validates origin and exact endpoint metadata before login."""

import pytest

from atellagent_client.onboarding.bootstrap_metadata import BootstrapMetadata
from atellagent_client.onboarding.errors import OnboardingClientError


def metadata():
    origin = "https://atellagent.example"
    issuer = origin + "/auth/realms/customer"
    return {"protocol_revision": 1, "platform_origin": origin, "issuer_url": issuer,
        "client_id": "atellagent-client", "audience": "atellagent-client",
        "scopes": ["openid", "profile", "email", "client.onboarding", "client.manage"],
        "device_authorization_url": issuer + "/protocol/openid-connect/auth/device",
        "token_url": issuer + "/protocol/openid-connect/token",
        "revocation_url": issuer + "/protocol/openid-connect/revoke",
        "browser_authorization_url": origin + "/app/device"}


def test_metadata_retains_trusted_origin_and_endpoints():
    payload = metadata()
    result = BootstrapMetadata.from_payload(payload, expected_origin=payload["platform_origin"])
    assert result.token_url == payload["token_url"]
    assert result.browser_authorization_url == payload["browser_authorization_url"]


@pytest.mark.parametrize("field,value", [("protocol_revision", True), ("client_id", "other"),
    ("issuer_url", "https://attacker.example/auth/realms/customer"),
    ("token_url", "https://attacker.example/token"), ("scopes", ["openid", "admin"]),
    ("platform_origin", "https://attacker.example"), ("unknown", "secret")])
def test_changed_metadata_is_not_used_for_login(field, value):
    payload = metadata()
    with pytest.raises(OnboardingClientError):
        BootstrapMetadata.from_payload({**payload, field: value}, expected_origin=payload["platform_origin"])
