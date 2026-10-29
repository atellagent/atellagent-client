"""Desktop bridge settings are reviewed, credential-free and conflict-safe."""

import json
import os
import subprocess
import sys
import tomllib
from unittest.mock import AsyncMock, Mock

import pytest

from atellagent_client.cli.native_hook_review import prepare
from atellagent_client.cli.native_hook_posix_apply import apply
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.proxy import cli
from atellagent_client.proxy.bridge_config import local_mcp_bridge_config
from test_native_hook_review import generated


def save(review):
    return apply(review, review_hash=review.public["review_sha256"], require_selection=lambda: None)


@pytest.mark.skipif(os.name != "posix", reason="POSIX settings writer")
@pytest.mark.parametrize("host", ["codex", "claude-code"])
def test_bridge_install_and_remove_preserve_unrelated_connectors_and_private_fields(tmp_path, host):
    home = tmp_path.resolve()
    proposal = generated(home, host)
    file = proposal["files"][1]
    path = home / (".codex/config.toml" if host == "codex" else ".claude.json")
    path.parent.mkdir(exist_ok=True)
    path.write_text('# retained\nmodel="test"\n[mcp_servers.customer]\ncommand="customer"\n' if host == "codex"
        else json.dumps({"oauthAccount": {"token": "private"}, "projects": {"/work": {"trust": True}},
            "mcpServers": {"customer": {"type": "http", "url": "https://customer.example/mcp"}}}))
    before = tomllib.loads(path.read_text()) if host == "codex" else json.loads(path.read_text())
    reviewed = prepare(proposal, home=home)
    assert "oauthAccount" not in json.dumps(reviewed.public)
    assert len(save(reviewed)["written"]) == 2
    installed = tomllib.loads(path.read_text()) if host == "codex" else json.loads(path.read_text())
    key = "mcp_servers" if host == "codex" else "mcpServers"
    assert installed[key]["customer"] == before[key]["customer"]
    assert installed[key]["atellagent"]["args"][-1] == proposal["endpoint"]
    if host == "claude-code":
        assert installed["oauthAccount"] == before["oauthAccount"]
    assert save(prepare(proposal, home=home))["written"] == []
    removed = prepare(proposal, home=home, remove=True)
    assert removed.public["files"][1]["removals"] == (2 if host == "codex" else 1)
    save(removed)
    after = tomllib.loads(path.read_text()) if host == "codex" else json.loads(path.read_text())
    if host == "codex":
        assert "# retained" in path.read_text()
        before["network"] = {"allow_unix_sockets": []}
    assert after == before


@pytest.mark.parametrize("host", ["codex", "claude-code"])
@pytest.mark.parametrize("remove", [False, True])
def test_same_name_different_destination_requires_review_not_replacement(tmp_path, host, remove):
    home = tmp_path.resolve()
    path = home / (".codex/config.toml" if host == "codex" else ".claude.json")
    path.parent.mkdir(exist_ok=True)
    source = '[mcp_servers.atellagent]\nurl="https://different.example/mcp"\n' if host == "codex" else '{"mcpServers":{"atellagent":{"type":"http","url":"https://different.example/mcp"}}}'
    path.write_text(source)
    with pytest.raises(OnboardingClientError, match="native_mcp_settings_conflict"):
        prepare(generated(home, host), home=home, remove=remove)
    assert path.read_text() == source


def test_claude_config_change_invalidates_exact_review_and_redacts_existing_credentials(tmp_path):
    home = tmp_path.resolve()
    reviewed = prepare(generated(home, "claude-code"), home=home)
    (home / ".claude.json").write_text('{"oauthAccount":{"token":"private"}}')
    with pytest.raises(OnboardingClientError, match="settings_changed"):
        reviewed.require_unchanged()
    fresh = prepare(generated(home, "claude-code"), home=home)
    assert "oauthAccount" not in json.dumps(fresh.public)


@pytest.mark.parametrize("endpoint", ["relative", "/", "/tmp/../socket", "//remote/socket", "/tmp/*", "/tmp/socket\n"])
def test_direct_bridge_rejects_nonlocal_or_ambiguous_endpoint(endpoint):
    with pytest.raises(ValueError):
        local_mcp_bridge_config(endpoint)


@pytest.mark.asyncio
async def test_direct_stdio_bridge_uses_selected_socket_and_preserves_arguments(monkeypatch, capsys):
    call = AsyncMock(return_value={"content": [{"type": "text", "text": "mock"}], "is_error": False})
    client = Mock()
    client.return_value.call = call
    monkeypatch.setattr(cli, "HookControlClient", client)
    request = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
        "name": "atellagent_call_tool", "arguments": {"server_id": "registered", "tool_id": "read",
            "arguments": {"record": "synthetic", "limit": 2}}}}
    monkeypatch.setattr(cli.sys, "stdin", [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"}}) + "\n", json.dumps(request) + "\n"])
    await cli._run_local_mcp_bridge(control_socket="/private/tmp/selected-controller.sock")
    client.assert_called_once_with("/private/tmp/selected-controller.sock", timeout_seconds=30.0)
    assert call.call_args.args[0] == "mcp.invoke"
    assert call.call_args.args[1]["arguments"] == request["params"]["arguments"]
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["id"] == 7


def test_packaged_cli_entry_supports_bridge_without_loading_ordinary_cli():
    code = "import sys; sys.modules['atellagent_client.cli.main']=None; from atellagent_client.cli.entry import main; main()"
    child = subprocess.run([sys.executable, "-c", code, "mcp-bridge", "--help"], capture_output=True, timeout=10)
    assert child.returncode == 0 and b"--socket" in child.stdout and b"--config" in child.stdout


@pytest.mark.parametrize("host", ["codex", "claude-code"])
@pytest.mark.parametrize("mutation", ["command", "args", "secret", "type"])
def test_bridge_fragment_is_closed_and_requires_literal_local_launch(tmp_path, host, mutation):
    home = tmp_path.resolve()
    proposal = generated(home, host)
    key = "mcp_servers" if host == "codex" else "mcpServers"
    entry = proposal["files"][1]["fragment"][key]["atellagent"]
    if mutation == "command": entry["command"] = "relative"
    elif mutation == "args": entry["args"] = ["mcp-bridge", "--socket", "https://different.example"]
    elif mutation == "secret": entry["env"] = {"TOKEN": "never copied"}
    else: entry["type"] = "http"
    with pytest.raises(OnboardingClientError, match="settings_invalid"):
        prepare(proposal, home=home)
    assert not (home / ".claude.json").exists()


@pytest.mark.parametrize("remove", [False, True])
def test_claude_prior_exact_launch_requires_reviewed_loading_update(tmp_path, remove):
    home = tmp_path.resolve()
    proposal = generated(home, "claude-code")
    new_entry = proposal["files"][1]["fragment"]["mcpServers"]["atellagent"]
    assert new_entry["alwaysLoad"] is True
    prior_entry = {k: v for k, v in new_entry.items() if k != "alwaysLoad"}
    path = home / ".claude.json"
    before = {"oauthAccount": {"token": "private"}, "mcpServers": {
        "atellagent": prior_entry, "customer": {"type": "http", "url": "https://customer.example/mcp"}}}
    path.write_text(json.dumps(before))
    review = prepare(proposal, home=home, remove=remove)
    assert json.loads(path.read_text()) == before
    assert "oauthAccount" not in json.dumps(review.public)
    assert review.public["files"][1]["changed"]
    assert review.public["files"][1]["removals" if remove else "additions"] == 1
    save(review)
    after = json.loads(path.read_text())
    assert after["oauthAccount"] == before["oauthAccount"]
    assert after["mcpServers"]["customer"] == before["mcpServers"]["customer"]
    if remove:
        assert "atellagent" not in after["mcpServers"]
    else:
        assert after["mcpServers"]["atellagent"] == new_entry
        assert prepare(proposal, home=home).public["files"][1]["changed"] is False


@pytest.mark.parametrize("preference", [False, 1, "true", None])
def test_claude_loading_preference_must_be_literal_true_and_existing_explicit_choices_conflict(tmp_path, preference):
    home = tmp_path.resolve()
    proposal = generated(home, "claude-code")
    entry = proposal["files"][1]["fragment"]["mcpServers"]["atellagent"]
    changed = {**entry, "alwaysLoad": preference}
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"atellagent": changed}}))
    with pytest.raises(OnboardingClientError, match="native_mcp_settings_conflict"):
        prepare(proposal, home=home)
    entry["alwaysLoad"] = preference
    with pytest.raises(OnboardingClientError, match="settings_invalid"):
        prepare(proposal, home=home)


def test_claude_old_review_without_loading_field_is_not_a_current_generated_definition(tmp_path):
    home = tmp_path.resolve()
    proposal = generated(home, "claude-code")
    del proposal["files"][1]["fragment"]["mcpServers"]["atellagent"]["alwaysLoad"]
    with pytest.raises(OnboardingClientError, match="settings_invalid"):
        prepare(proposal, home=home)


@pytest.mark.parametrize("value", [30.0, True, "30"])
def test_codex_saved_startup_type_cannot_alias_exact_generated_entry(tmp_path, value):
    home = tmp_path.resolve()
    (home / ".codex").mkdir()
    generated_entry = generated(home)["files"][1]["fragment"]["mcp_servers"]["atellagent"]
    startup = repr(value).lower() if type(value) is not str else '"' + value + '"'
    (home / ".codex/config.toml").write_text('[mcp_servers.atellagent]\ncommand=' + json.dumps(generated_entry["command"]) +
        '\nargs=' + json.dumps(generated_entry["args"]) + '\nstartup_timeout_sec=' + startup + '\n')
    with pytest.raises(OnboardingClientError, match="native_mcp_settings_conflict"):
        prepare(generated(home), home=home)
