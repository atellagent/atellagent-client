"""Generated hook fragments expose exact commands, never credentials or readiness."""

import base64
import json
import shlex
from types import SimpleNamespace
from uuid import uuid4

import pytest

from atellagent_client.cli.native_hook_definition import definition
from atellagent_client.cli.native_hook_commands import command


def draft(host):
    return SimpleNamespace(proposal=SimpleNamespace(id=uuid4(), native_host=host), device_id=uuid4())


@pytest.mark.parametrize("host", ["codex", "claude-code"])
def test_mac_fragments_use_guarded_literal_command_and_exact_socket(host):
    saved = draft(host)
    result = definition(draft=saved, executable="/Applications/Company's app/atellagent-cli",
        endpoint=f"/private/tmp/atellagent-501/{host}.sock", home="/Users/example", platform="darwin")
    hooks = result["files"][0]["fragment"]["hooks"]
    expected = {"UserPromptSubmit", "PreToolUse", "PostToolUse"}
    if host == "claude-code": expected.add("PostToolUseFailure")
    assert set(hooks) == expected
    for groups in hooks.values():
        handler = groups[0]["hooks"][0]
        assert shlex.split(handler["command"]) == result["command_argv"]
        assert handler["timeout"] == 20 and "async" not in handler
    assert result["readiness"] == "not_verified"
    assert result["request_id"] == str(saved.proposal.id)
    if host == "codex":
        assert result["files"][1]["fragment"]["network"] == {"allow_unix_sockets": [result["endpoint"]]}
        assert "statusMessage" in hooks["PreToolUse"][0]["hooks"][0]
    else: assert result["files"][1]["path"] == "/Users/example/.claude.json"
    bridge = result["files"][1]["fragment"]["mcp_servers" if host == "codex" else "mcpServers"]["atellagent"]
    assert bridge["command"] == result["command_argv"][0]
    assert bridge["args"] == ["mcp-bridge", "--socket", result["endpoint"]]
    if host == "claude-code":
        assert bridge["alwaysLoad"] is True
    else:
        assert "alwaysLoad" not in bridge


@pytest.mark.parametrize("host", ["codex", "claude-code"])
def test_windows_literal_script_quotes_paths_and_preserves_blocking_exit(host):
    result = definition(draft=draft(host), executable=r"C:\Users\O'Neil\Atellagent\atellagent-cli.exe",
        endpoint=rf"\\.\pipe\atellagent-user-{host}", home=r"C:\Users\O'Neil", platform="win32")
    handler = result["files"][0]["fragment"]["hooks"]["PreToolUse"][0]["hooks"][0]
    if host == "codex":
        script = base64.b64decode(handler["command"].split()[-1]).decode("utf-16-le")
        assert handler["command"].startswith("powershell.exe -NoProfile -NonInteractive -EncodedCommand ")
    else:
        script = handler["command"]
        assert handler["shell"] == "powershell"
    assert "O''Neil" in script and "if ($LASTEXITCODE -eq 0) { exit 0 }; exit 2" in script
    assert "could not start; request denied." in script
    assert "'hook' '--host'" in script and result["endpoint"] in script
    assert len(result["files"]) == 1


def test_definition_hash_changes_with_exact_command_or_selected_request():
    saved = draft("codex")
    args = dict(draft=saved, executable="/Applications/App/atellagent-cli", endpoint="/private/tmp/example.sock",
        home="/Users/example", platform="darwin")
    first = definition(**args)
    assert first == definition(**args)
    assert first["definition_sha256"] != definition(**{**args, "endpoint": "/private/tmp/other.sock"})["definition_sha256"]
    assert first["definition_sha256"] != definition(**{**args, "draft": draft("codex")})["definition_sha256"]


@pytest.mark.parametrize("executable,endpoint", [("relative-cli", "/tmp/control.sock"),
    ("/absolute/../cli", "/tmp/control.sock"), ("/absolute/cli\n", "/tmp/control.sock"),
    ("/absolute/cli", "http://localhost:9000"), ("/absolute/cli", "/tmp/../control.sock")])
def test_invalid_execution_paths_are_rejected(executable, endpoint):
    with pytest.raises(ValueError):
        command([executable, "hook", "--host", "codex", "--socket", endpoint], platform="darwin", host="codex")
