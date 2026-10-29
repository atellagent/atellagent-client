"""Review output contains generated changes, never existing settings content."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from atellagent_client.cli.native_hook_definition import definition
from atellagent_client.cli.native_hook_review import prepare
from atellagent_client.onboarding.errors import OnboardingClientError


def generated(home, host="codex"):
    draft = SimpleNamespace(proposal=SimpleNamespace(id=uuid4(), native_host=host), device_id=uuid4())
    return definition(draft=draft, executable="/Applications/Atellagent/cli",
        endpoint="/private/tmp/exact.sock", home=str(home), platform="darwin")


def test_review_redacts_existing_data_and_does_not_create_settings(tmp_path):
    home = tmp_path.resolve()
    definition = generated(home)
    first = prepare(definition, home=home)
    assert not (home / ".codex").exists()
    directory = home / ".codex"
    directory.mkdir()
    (directory / "hooks.json").write_text('{"env":{"SECRET":"private-value"}}')
    second = prepare(definition, home=home)
    assert first.public["review_sha256"] != second.public["review_sha256"]
    assert "private-value" not in json.dumps(second.public)
    assert "private-value" not in repr(second)
    assert json.loads(second.edits[0].after)["env"]["SECRET"] == "private-value"
    assert (directory / "hooks.json").read_text() == '{"env":{"SECRET":"private-value"}}'
    assert second.public["files"][0]["additions"] == 3
    assert second.public["readiness"] == "not_verified"


def test_any_post_review_edit_requires_new_review(tmp_path):
    home = tmp_path.resolve()
    reviewed = prepare(generated(home), home=home)
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('model="new"')
    with pytest.raises(OnboardingClientError, match="settings_changed"):
        reviewed.require_unchanged()


def test_existing_inline_hooks_block_json_plan(tmp_path):
    home = tmp_path.resolve()
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('[hooks]\n')
    with pytest.raises(OnboardingClientError, match="inline_settings_conflict"):
        prepare(generated(home), home=home)


@pytest.mark.parametrize("socket_edit", [True, False])
def test_codex_trust_state_is_preserved_without_inline_hook_conflict(tmp_path, socket_edit):
    home = tmp_path.resolve()
    directory = home / ".codex"
    directory.mkdir()
    state = b'[hooks.state."hooks.json:pre_tool_use:0:0"]\ntrusted_hash="sha256:example"\nenabled=false\n'
    (directory / "config.toml").write_bytes(state)
    proposed = generated(home)
    if not socket_edit:
        proposed["files"] = proposed["files"][:1]
    reviewed = prepare(proposed, home=home)
    assert (directory / "config.toml").read_bytes() == state
    if socket_edit:
        assert state in reviewed.edits[1].after
    else:
        assert len(reviewed.guards) == 1


def test_no_socket_edit_still_checks_inline_hook_source(tmp_path):
    home = tmp_path.resolve()
    proposed = generated(home)
    proposed["files"] = proposed["files"][:1]
    reviewed = prepare(proposed, home=home)
    assert len(reviewed.guards) == 1
    assert reviewed.public["checks"][0]["purpose"] == "inline_hooks_absent"
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('[hooks]\n')
    with pytest.raises(OnboardingClientError, match="settings_changed"):
        reviewed.require_unchanged()


def test_claude_reviews_hooks_and_separate_user_mcp_file(tmp_path):
    home = tmp_path.resolve()
    reviewed = prepare(generated(home, "claude-code"), home=home)
    assert len(reviewed.edits) == 2 and not reviewed.guards
    assert reviewed.edits[1].path == home / ".claude.json"
    assert reviewed.public["files"][1]["additions"] == 1
    assert reviewed.public["files"][0]["additions"] == 4
