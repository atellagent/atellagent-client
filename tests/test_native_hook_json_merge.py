"""Settings content stays intact; conflicts are never silently replaced."""

from copy import deepcopy
import json

import pytest

from atellagent_client.cli.native_hook_json_merge import LIMIT, merge
from atellagent_client.onboarding.errors import OnboardingClientError


def fragment():
    return {"hooks": {"PreToolUse": [{"hooks": [{"type": "command",
        "command": "/app/cli hook --host codex --socket /tmp/exact.sock", "timeout": 20}]}]}}


def test_preserves_unrelated_fields_and_handlers_without_mutating_fragment():
    existing = {"env": {"SECRET": "not-for-output"}, "permissions": {"allow": ["Read"]},
        "hooks": {"OtherEvent": [], "PreToolUse": [{"matcher": "Read", "hooks": [
            {"type": "command", "command": "other-command", "timeout": 3}]}]}}
    generated = fragment()
    saved = deepcopy(generated)
    output, count = merge(json.dumps(existing).encode(), generated)
    parsed = json.loads(output)
    assert count == 1 and generated == saved
    assert parsed["env"] == existing["env"] and parsed["permissions"] == existing["permissions"]
    assert parsed["hooks"]["PreToolUse"][0] == existing["hooks"]["PreToolUse"][0]
    assert parsed["hooks"]["OtherEvent"] == []
    assert merge(output, generated) == (output, 0)


def test_noop_preserves_exact_original_bytes():
    content = json.dumps(fragment(), separators=(",", ":")).encode() + b"  \n"
    assert merge(content, fragment()) == (content, 0)


@pytest.mark.parametrize("change", ["matcher", "timeout", "duplicate", "extra_handler"])
def test_same_command_with_different_semantics_requires_review(change):
    existing = fragment()
    group = existing["hooks"]["PreToolUse"][0]
    if change == "matcher": group["matcher"] = "Read"
    elif change == "timeout": group["hooks"][0]["timeout"] = 1
    elif change == "duplicate": existing["hooks"]["PreToolUse"].append(deepcopy(group))
    else: group["hooks"].append({"type": "command", "command": "other"})
    with pytest.raises(OnboardingClientError, match="settings_conflict"):
        merge(json.dumps(existing).encode(), fragment())


@pytest.mark.parametrize("content", [b'[]', b'{"secret":1,"secret":2}',
    b'{"env":{"secret":1,"secret":2}}', b'{"x":NaN}', b'\xff',
    b'{"hooks":null}', b'{"hooks":{"PreToolUse":{}}}',
    b'{"hooks":{"PreToolUse":[{"hooks":[null]}]}}', b" " * (LIMIT + 1)])
def test_invalid_or_ambiguous_settings_rejected_without_contents_in_error(content):
    with pytest.raises(OnboardingClientError) as error:
        merge(content, fragment())
    assert error.value.code == "onboarding_native_hook_settings_invalid"
    assert "secret" not in str(error.value)


def test_empty_settings_accept_generated_hooks():
    output, count = merge(b"{}", fragment())
    assert count == 1 and json.loads(output) == fragment()
