"""Shipping preview requires an exact local selection, without login or writes."""

import json
from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock, Mock

import pytest

from atellagent_client.cli import setup_native, setup_native_hooks
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_runtime_selection import NativeRuntimeSelections
from native_runtime_binding_support import runtime_case


@pytest.fixture
def case(tmp_path, monkeypatch):
    runtime = runtime_case(tmp_path)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(setup_native_hooks, "load_native_local_draft", lambda directory, request: runtime.case.draft)
    monkeypatch.setattr(setup_native_hooks.service, "_require_supported_platform", Mock())
    monkeypatch.setattr(setup_native_hooks.service, "_installed_cli_executable", lambda: Path("/absolute/atellagent-cli"))
    monkeypatch.setattr(setup_native_hooks.service, "_endpoint", lambda host: "/private/tmp/example.sock")
    monkeypatch.setattr(setup_native_hooks, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path.resolve()))
    slots = NativeRuntimeSelections(tmp_path / "native-runtime-selections")
    slots.select(draft=runtime.case.draft, binding=runtime.binding)
    return runtime, slots, tmp_path


@pytest.mark.asyncio
async def test_shipping_preview_is_read_only_and_json_only(case, monkeypatch, capsys):
    runtime, slots, directory = case
    monkeypatch.setattr(setup_native, "installation_directory", lambda: directory)
    no_login = AsyncMock(side_effect=AssertionError("Preview must not open browser or device transport"))
    monkeypatch.setattr(setup_native, "login", no_login)
    monkeypatch.setattr(setup_native.NativeSetupContext, "open", no_login)
    before = slots.load("codex")
    await setup_native.run(["hooks", str(runtime.case.draft.proposal.id), "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["readiness"] == "not_verified"
    assert slots.load("codex") == before
    no_login.assert_not_awaited()


def test_missing_or_changed_selection_and_custom_profile_never_use_default_settings(case, monkeypatch):
    runtime, slots, directory = case
    request = runtime.case.draft.proposal.id
    monkeypatch.setenv("CODEX_HOME", "/another/profile")
    with pytest.raises(OnboardingClientError, match="onboarding_native_hook_profile_requires_review"):
        setup_native_hooks.preview(directory, request)
    monkeypatch.delenv("CODEX_HOME")
    slots.remove(draft=runtime.case.draft)
    with pytest.raises(OnboardingClientError, match="onboarding_native_request_not_selected"):
        setup_native_hooks.preview(directory, request)


def test_selection_changed_during_preview_is_rejected(case, monkeypatch):
    runtime, slots, directory = case
    render = setup_native_hooks.definition
    def removing(**values):
        result = render(**values)
        slots.remove(draft=runtime.case.draft)
        return result
    monkeypatch.setattr(setup_native_hooks, "definition", removing)
    with pytest.raises(OnboardingClientError, match="onboarding_native_selection_changed"):
        setup_native_hooks.preview(directory, runtime.case.draft.proposal.id)


@pytest.mark.asyncio
async def test_shipping_explicit_apply_is_json_only_and_does_not_open_login(case, monkeypatch, capsys):
    runtime, slots, directory = case
    monkeypatch.setattr(setup_native, "installation_directory", lambda: directory)
    no_login = AsyncMock(side_effect=AssertionError("Settings apply must not open login"))
    monkeypatch.setattr(setup_native, "login", no_login)
    monkeypatch.setattr(setup_native.NativeSetupContext, "open", no_login)
    reviewed = setup_native_hooks.preview(directory, runtime.case.draft.proposal.id)
    await setup_native.run(["hooks", str(runtime.case.draft.proposal.id), "--json",
        "--apply-review", reviewed["review_sha256"]])
    result = json.loads(capsys.readouterr().out)
    assert result["settings"] == "saved" and result["readiness"] == "not_verified"
    assert len(result["written"]) == 2
    assert slots.load("codex") is not None
    no_login.assert_not_awaited()


def test_stale_preview_cannot_be_applied(case):
    runtime, slots, directory = case
    request = runtime.case.draft.proposal.id
    reviewed = setup_native_hooks.preview(directory, request)
    settings = Path.home() / ".codex"
    settings.mkdir()
    (settings / "hooks.json").write_text('{"env":{"USER":"edited"}}')
    with pytest.raises(OnboardingClientError, match="review_changed"):
        setup_native_hooks.apply(directory, request, reviewed["review_sha256"])
    assert json.loads((settings / "hooks.json").read_text()) == {"env": {"USER": "edited"}}


@pytest.mark.parametrize("host", ["codex", "claude-code"])
def test_hook_verification_ignores_mcp_and_unrelated_preferences(case, monkeypatch, host):
    from atellagent_client.cli.native_hook_setup_status import status
    from atellagent_client.integrations.agents.hook_verification import record_accepted

    runtime, slots, directory = case
    if host != "codex":
        runtime.case.draft = replace(runtime.case.draft,
            proposal=replace(runtime.case.draft.proposal, native_host=host))
        slots.select(draft=runtime.case.draft, binding=runtime.binding)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    request = runtime.case.draft.proposal.id
    reviewed, _ = setup_native_hooks._review(directory, request, hooks_only=True)
    for edit in reviewed.edits:
        edit.path.parent.mkdir(exist_ok=True)
        edit.path.write_bytes(edit.after)
    endpoint = directory / "controller.sock"
    record_accepted(endpoint, runtime.binding, f"local:{runtime.binding.authorization_id}:1:opaque",
        dict(received_at=int(time.time()), outcome=dict(outcome_observation="result_observed", success=True)))
    delegation = dict(authorization_id=str(runtime.binding.authorization_id), authorization_epoch=1)
    assert status(directory, runtime.case.draft, endpoint, delegation) == "verified"

    # Rewrite preferences after the accepted result and deliberately configure
    # a conflicting MCP entry. Neither changes the selected native hooks.
    if host == "codex":
        preferences = directory / ".codex" / "config.toml"
        with preferences.open("a") as stream:
            stream.write('\n[mcp_servers.atellagent]\ncommand="customer-mcp"\nargs=[]\n')
    else:
        preferences = directory / ".claude.json"
        preferences.write_text('{"mcpServers":{"atellagent":{"command":"customer-mcp"}}}')
    os.utime(preferences, (time.time() + 5, time.time() + 5))
    with pytest.raises(OnboardingClientError, match="mcp_settings_conflict"):
        setup_native_hooks.preview(directory, request)
    assert status(directory, runtime.case.draft, endpoint, delegation) == "verified"

    hook_file = reviewed.edits[0].path
    content = json.loads(hook_file.read_text())
    content["theme"] = "customer-preference"
    hook_file.write_text(json.dumps(content))
    os.utime(hook_file, (time.time() + 5, time.time() + 5))
    assert status(directory, runtime.case.draft, endpoint, delegation) == "verified"
    del content["hooks"]["PostToolUse"]
    hook_file.write_text(json.dumps(content))
    assert status(directory, runtime.case.draft, endpoint, delegation) == "settings_required"


def test_hook_verification_still_requires_codex_socket_permission(case):
    runtime, slots, directory = case
    request = runtime.case.draft.proposal.id
    reviewed, _ = setup_native_hooks._review(directory, request, hooks_only=True)
    for edit in reviewed.edits:
        edit.path.parent.mkdir(exist_ok=True)
        edit.path.write_bytes(edit.after)
    (directory / ".codex" / "config.toml").write_text('model="customer"\n')
    reviewed, _ = setup_native_hooks._review(directory, request, hooks_only=True)
    assert reviewed.public["files"][1]["changed"]
    assert reviewed.public["files"][1]["fragment"] == {
        "network": {"allow_unix_sockets": ["/private/tmp/example.sock"]}}
