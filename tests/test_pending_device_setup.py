"""Restart/lost-response recovery keeps the original exact device setup."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest

from atellagent_client.onboarding.device_installation import DeviceInstallation
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.pending_device_resume import resume_pending_device_setup, submit_pending_device_setup
from atellagent_client.onboarding.pending_device_setup import PendingDeviceSetupStore
from device_certificate_support import certificate_case


def prepared(tmp_path):
    draft, receipt, _, _ = certificate_case()
    installation = DeviceInstallation(tmp_path / "installation")
    installation.setups.save_initial(draft)
    pending = PendingDeviceSetupStore(installation)
    pending.begin(draft, reporting_enabled=False)
    return pending, draft, receipt


def test_saved_request_retains_keys_reporting_choice_and_assignment(tmp_path):
    pending, draft, receipt = prepared(tmp_path)
    pending.bind_receipt(receipt)
    pending.begin(draft, reporting_enabled=False)
    reopened = PendingDeviceSetupStore(DeviceInstallation(pending.installation.directory))
    current, assigned, enabled = reopened.load()
    assert current.encode() == draft.encode() and assigned == receipt and enabled is False
    raw = pending.path.read_text()
    assert "PRIVATE KEY" not in raw and "access_token" not in raw
    with pytest.raises(OnboardingClientError, match="identity_changed"):
        pending.bind_receipt(replace(receipt, device_id=uuid4()))


@pytest.mark.asyncio
async def test_lost_receipt_write_recovers_by_proof_without_management_login(tmp_path, monkeypatch):
    from test_device_setup_flow import fixture
    flow, state, _ = fixture(tmp_path, monkeypatch)
    pending = PendingDeviceSetupStore(flow.installation)
    pending.begin(flow.draft, reporting_enabled=False)
    class Management:
        async def submit(self, *, draft, store):
            assert draft.encode() == flow.draft.encode()
            assert store.require_saved(draft, expected_origin=draft.platform_origin).encode() == draft.encode()
            return flow.receipt
    original = pending.bind_receipt
    def failed_bind(receipt):
        raise OSError("simulated lost local submission acknowledgement")
    monkeypatch.setattr(pending, "bind_receipt", failed_bind)
    with pytest.raises(OSError):
        await submit_pending_device_setup(Management(), pending)
    assert pending.load()[1] is None
    monkeypatch.setattr(pending, "bind_receipt", original)
    resumed = await resume_pending_device_setup(pending)
    assert resumed.draft.encode() == flow.draft.encode()
    assert resumed.receipt.device_id == flow.receipt.device_id and not resumed.reporting_enabled
    assert (await resumed.advance()).stage == "awaiting_approval"
    assert len(state["requests"]) == 2
    assert pending.load()[1].device_id == flow.receipt.device_id


@pytest.mark.parametrize("field,value", [("revision", True), ("owner_user_id", str(uuid4())),
    ("reporting_enabled", "false"), ("extra", "ignored")])
def test_changed_or_open_pending_record_cannot_resume(tmp_path, field, value):
    pending, _, _ = prepared(tmp_path)
    payload = json.loads(pending.path.read_bytes())
    payload[field] = value
    pending.path.write_text(json.dumps(payload))
    with pytest.raises(OnboardingClientError):
        pending.load()


def test_conflicting_request_is_not_silently_replaced(tmp_path):
    pending, draft, _ = prepared(tmp_path)
    with pytest.raises(OnboardingClientError, match="pending_setup_conflict"):
        pending.begin(draft, reporting_enabled=True)
    original = pending.path.read_bytes()
    other = replace(draft, operation_id=uuid4())
    pending.installation.setups.save_initial(other)
    with pytest.raises(OnboardingClientError, match="pending_setup_conflict"):
        pending.begin(other, reporting_enabled=False)
    assert pending.path.read_bytes() == original
