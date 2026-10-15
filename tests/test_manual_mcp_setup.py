"""Manual targets reuse local safety validation and need no host configuration."""

import sys
import pytest

from atellagent_client.cli.setup_mcp_options import options
from atellagent_client.cli.setup_mcp_target import selected_target
from atellagent_client.discovery.mcp_connection import target_inputs
from atellagent_client.onboarding.mcp_manual_target import manual_target


def test_manual_http_needs_no_host_or_configuration():
    args = options(["request", "--name", "Mock", "--target-url", "http://127.0.0.1:8787/mcp"])
    inputs = target_inputs(selected_target(args))
    assert inputs.deployment["target_url"] == "http://127.0.0.1:8787/mcp"
    assert args.host is None


def test_manual_stdio_preserves_arguments_without_shell_parsing():
    args = options(["request", "--name", "Tools", "--target-command", sys.executable,
        "--target-arg=--stdio", "--target-arg=argument with spaces"])
    inputs = target_inputs(selected_target(args))
    assert inputs.deployment["target_args"] == ["--stdio", "argument with spaces"]


@pytest.mark.parametrize("url", ["https://example.com/mcp", "http://user:secret@127.0.0.1:8787/mcp"])
def test_manual_http_does_not_expand_the_local_target_boundary(url):
    with pytest.raises(ValueError):
        target_inputs(manual_target(url=url))


@pytest.mark.parametrize("extra", [["--host", "codex"], ["--source", "codex-user"], ["--target-arg=--stdio"]])
def test_manual_http_rejects_unrelated_selector_inputs(extra):
    with pytest.raises(SystemExit):
        options(["request", "--name", "Mock", "--target-url", "http://127.0.0.1:8787/mcp", *extra])


def test_manual_credentials_are_environment_references(monkeypatch):
    monkeypatch.setenv("TOOLS_CREDENTIAL", "test-only")
    mappings = {"API_KEY": "TOOLS_CREDENTIAL"}
    inputs = target_inputs(manual_target(command=sys.executable,
        credential_environment_names=mappings), credential_environment_names=mappings)
    assert inputs.deployment["target_env_map"] == mappings
    assert "test-only" not in str(inputs.payload())
