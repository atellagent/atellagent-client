"""Closed device responses and HTTPS origins do not silently accept other formats."""

from uuid import uuid4

import pytest

from atellagent_client.onboarding.device_status import DeviceStatus
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.origins import https_origin
from onboarding_support import identity, metadata


@pytest.mark.parametrize("origin", ["http://device.example", "https://user:secret@device.example",
    "https://device.example/path", "https://device.example?token=secret", "https://device.example#fragment",
    " https://device.example", "https://device.example:invalid"])
def test_origin_rejects_unsafe_or_ambiguous_destinations(origin):
    with pytest.raises(ValueError):
        https_origin(origin)


@pytest.mark.parametrize("change", [{"unexpected": "secret"}, {"discovery_fresh": 1},
    {"certificate_expires_at": "2020-01-01T00:00:00Z"}, {"server_time": "2026-10-01T00:00:00"},
    {"device_name": " bad label "}, {"tenant_name": "line\nbreak"}, {"status": "pending"}])
def test_status_rejects_inconsistent_or_unknown_response(tmp_path, change):
    device = identity(tmp_path / "device", tenant=uuid4(), device=uuid4(), owner=uuid4())
    with pytest.raises(OnboardingClientError) as error:
        DeviceStatus.from_payload({**metadata(device), **change})
    assert error.value.code == "device_response_invalid"
