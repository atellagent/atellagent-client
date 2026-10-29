"""TOML edits preserve unrelated values/comments and reject overlapping hooks."""

import tomllib

import pytest

from atellagent_client.cli.native_hook_toml_merge import merge, require_json_hook_source
from atellagent_client.onboarding.errors import OnboardingClientError

FRAGMENT = {"network": {"allow_unix_sockets": ["/private/tmp/atellagent-501/codex.sock"]}}


@pytest.mark.parametrize("source", [
    '# user comment\nmodel = "example" # model note\n',
    '[network] # permissions\nallow_unix_sockets = [\n  "/tmp/other.sock", # keep\n]\n',
    'network = { allow_unix_sockets = ["/tmp/other.sock"], enabled = true } # inline\n',
    'network.enabled = true\n[profiles.example]\nmodel = "other"\n',
    '[[mcp_servers.items]]\nname = "other" # unrelated\n',
    '[hooks.state."/home/example/.codex/hooks.json:pre_tool_use:0:0"]\ntrusted_hash = "sha256:example"\nenabled = true # host choice\n',
])
def test_only_socket_list_changes_and_comments_remain(source):
    output, count = merge(source.encode(), FRAGMENT)
    before = tomllib.loads(source)
    expected = dict(before)
    expected["network"] = dict(before.get("network", {}))
    expected["network"]["allow_unix_sockets"] = before.get("network", {}).get("allow_unix_sockets", []) + FRAGMENT["network"]["allow_unix_sockets"]
    assert count == 1 and tomllib.loads(output.decode()) == expected
    for line in source.splitlines():
        if "#" in line: assert line[line.index("#"):] in output.decode()
    assert merge(output, FRAGMENT) == (output, 0)


def test_existing_socket_returns_original_bytes():
    original = b'[network]\nallow_unix_sockets=["/private/tmp/atellagent-501/codex.sock"] # retained\n'
    assert merge(original, FRAGMENT) == (original, 0)


@pytest.mark.parametrize("source", [b'[hooks]\n', b'[[hooks.PreToolUse]]\nmatcher="Read"\n', b'hooks = false\n',
    b'[hooks.state]\n[hooks.PreToolUse]\n', b'[hooks]\nstate = false\n'])
def test_inline_hook_source_requires_review_even_if_empty(source):
    with pytest.raises(OnboardingClientError, match="inline_settings_conflict"):
        require_json_hook_source(source)
    with pytest.raises(OnboardingClientError, match="inline_settings_conflict"):
        merge(source, FRAGMENT)


@pytest.mark.parametrize("source", [b'network=false', b'[network]\nallow_unix_sockets=false',
    b'[network]\nallow_unix_sockets=[4]', b'secret="a"\nsecret="b"', b'\xff'])
def test_invalid_settings_error_does_not_reveal_values(source):
    with pytest.raises(OnboardingClientError) as error:
        merge(source, FRAGMENT)
    assert error.value.code == "onboarding_native_hook_settings_invalid"
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("endpoint", ["relative", "/", "/tmp/../socket", "/tmp/socket\n", "/tmp/*", "//server/socket"])
def test_permission_never_broadens_to_invalid_endpoint(endpoint):
    with pytest.raises(OnboardingClientError):
        merge(b"", {"network": {"allow_unix_sockets": [endpoint]}})
