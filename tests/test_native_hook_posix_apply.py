"""Explicit apply to disposable profiles, never the installed host settings."""

import json
import os
from unittest.mock import Mock

import pytest

from atellagent_client.cli import native_hook_posix_apply as writer
from atellagent_client.cli.native_hook_review import prepare
from atellagent_client.onboarding.errors import OnboardingClientError
from test_native_hook_review import generated

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX atomic replacement")


def test_apply_creates_settings_preserves_values_and_requires_fresh_review(tmp_path):
    home = tmp_path.resolve()
    proposal = generated(home)
    reviewed = prepare(proposal, home=home)
    selected = Mock()
    result = writer.apply(reviewed, review_hash=reviewed.public["review_sha256"], require_selection=selected)
    assert result["settings"] == "saved" and result["readiness"] == "not_verified"
    assert len(result["written"]) == 2
    assert (home / ".codex" / "hooks.json").stat().st_mode & 0o777 == 0o600
    again = prepare(proposal, home=home)
    assert writer.apply(again, review_hash=again.public["review_sha256"], require_selection=selected)["written"] == []
    assert not list((home / ".codex").glob(".atellagent-hook-*"))


def test_wrong_review_or_user_edit_never_overwrites(tmp_path):
    home = tmp_path.resolve()
    reviewed = prepare(generated(home), home=home)
    with pytest.raises(OnboardingClientError, match="review_changed"):
        writer.apply(reviewed, review_hash="incorrect", require_selection=Mock())
    assert not (home / ".codex").exists()
    (home / ".codex").mkdir()
    path = home / ".codex" / "hooks.json"
    path.write_text('{"env":{"SECRET":"keep"}}')
    with pytest.raises(writer.ApplyError, match="settings_changed") as error:
        writer.apply(reviewed, review_hash=reviewed.public["review_sha256"], require_selection=Mock())
    assert error.value.written == ()
    assert json.loads(path.read_text())["env"]["SECRET"] == "keep"


def test_failed_second_commit_reports_first_without_rollback(tmp_path, monkeypatch):
    home = tmp_path.resolve()
    reviewed = prepare(generated(home), home=home)
    replace = writer.os.replace
    calls = 0
    def failing(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("sensitive filesystem detail")
        return replace(*args, **kwargs)
    monkeypatch.setattr(writer.os, "replace", failing)
    with pytest.raises(writer.ApplyError) as error:
        writer.apply(reviewed, review_hash=reviewed.public["review_sha256"], require_selection=Mock())
    assert error.value.written == (str(home / ".codex" / "hooks.json"),)
    assert "sensitive" not in str(error.value)
    assert not (home / ".codex" / "config.toml").exists()
    assert not list((home / ".codex").glob(".atellagent-hook-*"))


def test_selection_loss_during_staging_prevents_commit(tmp_path):
    home = tmp_path.resolve()
    reviewed = prepare(generated(home), home=home)
    selected = Mock(side_effect=[None, OnboardingClientError("onboarding_native_selection_changed")])
    with pytest.raises(writer.ApplyError) as error:
        writer.apply(reviewed, review_hash=reviewed.public["review_sha256"], require_selection=selected)
    assert error.value.written == ()
    assert not (home / ".codex" / "hooks.json").exists()


def test_existing_values_and_reviewed_mode_are_retained(tmp_path):
    home = tmp_path.resolve()
    (home / ".codex").mkdir()
    path = home / ".codex" / "hooks.json"
    path.write_text('{"env":{"SECRET":"retain"}}')
    path.chmod(0o640)
    reviewed = prepare(generated(home), home=home)
    writer.apply(reviewed, review_hash=reviewed.public["review_sha256"], require_selection=Mock())
    assert path.stat().st_mode & 0o777 == 0o640
    assert json.loads(path.read_text())["env"]["SECRET"] == "retain"
