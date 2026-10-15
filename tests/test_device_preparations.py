"""Desired settings and exact advancing pages cannot carry execution authority."""

from hashlib import sha256
from uuid import UUID, uuid4

import pytest

from atellagent_client.onboarding.device_preparations import DevicePreparationPage
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.proofs import canonical_json


def preparation(identifier=None):
    identifier = uuid4() if identifier is None else identifier
    settings = dict(display_name="Staff Codex", profile=dict(account_type="agent", packaging="sdk",
        native_host="codex"), enforcement_mode="enforce", coverage_posture="strict")
    return dict(assignment_id=str(identifier), status="awaiting_proposal", settings=settings,
        settings_sha256=sha256(canonical_json(settings)).hexdigest(), proposed_request_id=None,
        expires_at="2099-10-01T00:00:00+00:00")


def test_exact_preparation_is_settings_only_and_round_trips():
    value = preparation()
    result = DevicePreparationPage.from_payload(dict(preparations=[value], next_after=None))
    assert result.preparations[0].payload() == value
    assert "tls_public_key_der_base64" not in value["settings"]


@pytest.mark.parametrize("change", ["extra", "digest", "profile", "request", "name", "status"])
def test_changed_intent_or_claimed_authority_is_rejected(change):
    value = preparation()
    if change == "extra": value["approved"] = True
    elif change == "digest": value["settings_sha256"] = "a" * 64
    elif change == "profile": value["settings"]["profile"]["account_type"] = "model"
    elif change == "request": value.update(status="proposed", proposed_request_id=str(uuid4()))
    elif change == "name": value["settings"]["display_name"] = " hidden "
    else: value["status"] = "connected"
    with pytest.raises(OnboardingClientError):
        DevicePreparationPage.from_payload(dict(preparations=[value], next_after=None))


def test_keyset_page_requires_exact_last_cursor_and_advancing_unique_ids():
    values = [preparation(UUID(int=index)) for index in range(1, 9)]
    page = DevicePreparationPage.from_payload(dict(preparations=values, next_after=str(UUID(int=8))), after=UUID(int=0))
    assert len(page.preparations) == 8 and page.next_after == UUID(int=8)
    for invalid in ([values[0], values[0]], list(reversed(values)), values + [preparation(UUID(int=9))]):
        with pytest.raises(OnboardingClientError):
            DevicePreparationPage.from_payload(dict(preparations=invalid, next_after=None))
    with pytest.raises(OnboardingClientError):
        DevicePreparationPage.from_payload(dict(preparations=values, next_after=str(UUID(int=7))))


def test_exact_selector_cannot_adopt_another_preparation():
    with pytest.raises(OnboardingClientError):
        DevicePreparationPage.from_payload(dict(preparations=[preparation()], next_after=None), assignment_id=uuid4())
