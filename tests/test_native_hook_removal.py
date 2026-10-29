"""Reviewed local removal preserves customer settings and rejects stale plans."""

import json
import os
import tomllib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from atellagent_client.cli.native_hook_definition import definition
from atellagent_client.cli.native_hook_review import prepare
from atellagent_client.cli.native_hook_posix_apply import apply
from atellagent_client.onboarding.errors import OnboardingClientError


def generated(home, host):
    draft = SimpleNamespace(proposal=SimpleNamespace(id=uuid4(), native_host=host), device_id=uuid4())
    return definition(draft=draft, executable="/Applications/Atellagent/cli",
        endpoint="/private/tmp/exact.sock", home=str(home), platform="darwin")


@pytest.mark.skipif(os.name != "posix", reason="POSIX atomic settings writer")
@pytest.mark.parametrize("host", ["codex", "claude-code"])
def test_install_then_reviewed_remove_preserves_unrelated_settings(tmp_path, host):
    home = tmp_path.resolve()
    directory = home / (".codex" if host == "codex" else ".claude")
    directory.mkdir()
    path = directory / ("hooks.json" if host == "codex" else "settings.json")
    other = {"hooks": [{"type": "command", "command": "customer-hook", "timeout": 10}]}
    path.write_text(json.dumps({"private": "not displayed", "hooks": {"PreToolUse": [other]}}))
    if host == "codex":
        (directory / "config.toml").write_text('# retained comment\nmodel="customer"\n[network]\nallow_unix_sockets=["/tmp/customer.sock"]\n')
    proposed = generated(home, host)
    install = prepare(proposed, home=home)
    apply(install, review_hash=install.public["review_sha256"], require_selection=lambda: None)
    remove = prepare(proposed, home=home, remove=True)
    assert remove.public["operation"] == "remove"
    assert "not displayed" not in json.dumps(remove.public)
    assert remove.public["files"][0]["removals"] == (3 if host == "codex" else 4)
    with pytest.raises(OnboardingClientError, match="review_changed"):
        apply(remove, review_hash=install.public["review_sha256"], require_selection=lambda: None)
    apply(remove, review_hash=remove.public["review_sha256"], require_selection=lambda: None)
    assert json.loads(path.read_text()) == {"private": "not displayed", "hooks": {"PreToolUse": [other]}}
    if host == "codex":
        content = (directory / "config.toml").read_text()
        assert "# retained comment" in content
        assert tomllib.loads(content) == {"model": "customer", "network": {"allow_unix_sockets": ["/tmp/customer.sock"]}}
    second = prepare(proposed, home=home, remove=True)
    assert not any(item["changed"] for item in second.public["files"])
    assert apply(second, review_hash=second.public["review_sha256"], require_selection=lambda: None)["written"] == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX atomic settings writer")
def test_absent_removal_does_not_create_host_directory(tmp_path):
    home = tmp_path.resolve()
    review = prepare(generated(home, "codex"), home=home, remove=True)
    assert apply(review, review_hash=review.public["review_sha256"], require_selection=lambda: None)["written"] == []
    assert not (home / ".codex").exists()


def test_modified_generated_command_is_not_removed(tmp_path):
    home = tmp_path.resolve()
    proposed = generated(home, "claude-code")
    directory = home / ".claude"
    directory.mkdir()
    content = proposed["files"][0]["fragment"]
    content["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 99
    path = directory / "settings.json"
    path.write_text(json.dumps(content))
    # Regenerate the original contract; a user-modified matching command is a
    # conflict, not evidence that its enclosing settings can be deleted.
    proposed["files"][0]["fragment"]["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 20
    with pytest.raises(OnboardingClientError, match="settings_conflict"):
        prepare(proposed, home=home, remove=True)
    assert json.loads(path.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] == 99
