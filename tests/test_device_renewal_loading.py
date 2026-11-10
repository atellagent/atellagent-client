"""Archived predecessors never authorize TLS; final selected material must be current."""

from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest

from atellagent_client.onboarding import device_renewal_loading as loading
from atellagent_client.onboarding.device_renewal_stage import stage_device_renewal, select_device_renewal
from atellagent_client.onboarding.errors import OnboardingClientError
from test_device_renewal_stage import renewal_case


def selected_case(tmp_path, *, select=True):
    device, trust, prepared, payload = renewal_case(tmp_path, leaf_days=1)
    staged = stage_device_renewal(device=device, trust=trust, prepared=prepared, payload=payload)
    if select:
        receipt = {name: payload[name] for name in ("operation_id", "device_id", "certificate_sha256", "public_key_sha256", "expires_at")}
        select_device_renewal(staged=staged, receipt={**receipt, "status": "activated"})
    return device, trust, staged


def future_clock(monkeypatch, days):
    future = datetime.now(timezone.utc) + timedelta(days=days)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return future
    monkeypatch.setattr(loading, "datetime", Clock)


def test_selected_replacement_loads_after_original_expires(tmp_path, monkeypatch):
    device, trust, staged = selected_case(tmp_path)
    future_clock(monkeypatch, 2)
    current = loading.load_selected_device_renewals(device=device, trust=trust)
    assert current.certificate_path == staged.certificate_path
    assert current.private_key_path == staged.private_key_path
    assert current.proof_key is device.proof_key
    assert current.owner_user_id == device.owner_user_id


def test_unselected_candidate_cannot_revive_expired_original(tmp_path, monkeypatch):
    device, trust, _ = selected_case(tmp_path, select=False)
    future_clock(monkeypatch, 2)
    with pytest.raises(OnboardingClientError):
        loading.load_selected_device_renewals(device=device, trust=trust)


def test_expired_final_replacement_is_not_runtime_authority(tmp_path, monkeypatch):
    device, trust, _ = selected_case(tmp_path)
    future_clock(monkeypatch, 31)
    with pytest.raises(OnboardingClientError):
        loading.load_selected_device_renewals(device=device, trust=trust)


@pytest.mark.parametrize("case", ["owner", "parent", "path", "candidate", "key"])
def test_changed_selected_history_fails_without_repair(tmp_path, case):
    device, trust, staged = selected_case(tmp_path)
    selection = staged.pointer.parent / "device-renewed.json"
    record = json.loads(selection.read_bytes())
    if case == "owner": record["owner_user_id"] = str(uuid4())
    elif case == "parent": record["parent_certificate_sha256"] = "0" * 64
    elif case == "path": record["generation"] = "../other"
    elif case == "candidate": staged.pointer.write_bytes(b"{}")
    else: staged.private_key_path.write_bytes(b"invalid")
    selection.write_bytes(json.dumps(record).encode() if case in {"owner", "parent", "path"} else staged.record)
    before = selection.read_bytes()
    with pytest.raises(OnboardingClientError):
        loading.load_selected_device_renewals(device=device, trust=trust)
    assert selection.read_bytes() == before
