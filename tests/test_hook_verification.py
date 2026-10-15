"""Only accepted results for the selected request complete hook setup."""

import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from atellagent_client.integrations.agents import hook_verification as receipt
from atellagent_client.cli import native_hook_setup_status as setup


def test_accepted_result_is_request_and_grant_scoped(tmp_path):
    tmp_path.chmod(0o700)
    endpoint = tmp_path / "controller.sock"
    binding = SimpleNamespace(request_id=uuid4(), authorization_id=uuid4(), authorization_epoch=1)
    action = f"local:{binding.authorization_id}:1:opaque"
    entry = dict(received_at=int(time.time()), outcome=dict(outcome_observation="result_observed", success=True))
    assert receipt.load(endpoint) is None
    receipt.record_accepted(endpoint, binding, action, entry)
    loaded = receipt.load(endpoint)
    assert loaded["request_id"] == str(binding.request_id)
    assert loaded["observed_at"] <= loaded["accepted_at"]
    original = loaded.copy()
    receipt.record_accepted(endpoint, binding, f"local:{uuid4()}:1:opaque", entry)
    receipt.record_accepted(endpoint, binding, action, {**entry, "outcome": dict(outcome_observation="execution_failed", success=False)})
    assert receipt.load(endpoint) == original


def test_setup_requires_current_settings_and_accepted_observation(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    endpoint = tmp_path / "controller.sock"
    binding = SimpleNamespace(request_id=uuid4(), authorization_id=uuid4(), authorization_epoch=1)
    settings = tmp_path / "hooks.json"
    settings.write_text("{}")
    review = SimpleNamespace(public=dict(files=[dict(changed=True)]), edits=[SimpleNamespace(path=settings)])
    monkeypatch.setattr(setup, "_review", lambda *args, **kwargs: (review, None))
    draft = SimpleNamespace(proposal=SimpleNamespace(id=binding.request_id))
    delegation = dict(authorization_id=str(binding.authorization_id), authorization_epoch=1)
    assert setup.status(tmp_path, draft, endpoint, delegation) == "settings_required"
    review.public["files"][0]["changed"] = False
    assert setup.status(tmp_path, draft, endpoint, delegation) == "waiting_for_tool_result"
    receipt.record_accepted(endpoint, binding, f"local:{binding.authorization_id}:1:opaque",
        dict(received_at=int(time.time()), outcome=dict(outcome_observation="result_observed", success=None)))
    assert setup.status(tmp_path, draft, endpoint, delegation) == "verified"
    assert setup.status(tmp_path, draft, endpoint, {**delegation, "authorization_epoch": 2}) == "waiting_for_tool_result"
    # Rewriting host preferences does not invalidate unchanged managed hooks.
    os.utime(settings, (time.time() + 3, time.time() + 3))
    assert setup.status(tmp_path, draft, endpoint, delegation) == "verified"
    review.public["files"][0]["changed"] = True
    assert setup.status(tmp_path, draft, endpoint, delegation) == "settings_required"


@pytest.mark.parametrize("change", ["request", "authorization", "epoch", "controller_unavailable"])
def test_verified_result_cannot_complete_another_registration(tmp_path, monkeypatch, change):
    tmp_path.chmod(0o700)
    endpoint = tmp_path / "controller.sock"
    binding = SimpleNamespace(request_id=uuid4(), authorization_id=uuid4(), authorization_epoch=1)
    receipt.record_accepted(endpoint, binding, f"local:{binding.authorization_id}:1:opaque",
        dict(received_at=int(time.time()), outcome=dict(outcome_observation="result_observed", success=True)))
    review = SimpleNamespace(public=dict(files=[dict(changed=False)]))
    def current_review(*args, hooks_only):
        assert hooks_only
        return review, None
    monkeypatch.setattr(setup, "_review", current_review)
    draft = SimpleNamespace(proposal=SimpleNamespace(id=binding.request_id))
    delegation = dict(authorization_id=str(binding.authorization_id), authorization_epoch=1)
    if change == "request":
        draft.proposal.id = uuid4()
    elif change == "authorization":
        delegation["authorization_id"] = str(uuid4())
    elif change == "epoch":
        delegation["authorization_epoch"] = 2
    else:
        delegation = None
    assert setup.status(tmp_path, draft, endpoint, delegation) == "waiting_for_tool_result"
