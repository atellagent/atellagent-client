"""Frozen hooks use an explicit worker, never executable-as-interpreter tricks."""

import io
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from atellagent_client import hook_launcher


@pytest.mark.parametrize("windows", [False, True])
def test_frozen_guard_preserves_payload_and_uses_windowless_worker(monkeypatch, windows):
    payload = b'{"hook_event_name":"PreToolUse","tool_name":"Read"}'
    output, errors = io.BytesIO(), io.BytesIO()
    call = Mock(return_value=SimpleNamespace(returncode=0, stdout=b"decision", stderr=b""))
    monkeypatch.setattr(hook_launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hook_launcher.sys, "executable", "/absolute/bundled-client")
    monkeypatch.setattr(hook_launcher.sys, "platform", "win32" if windows else "darwin")
    monkeypatch.setattr(hook_launcher.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(hook_launcher.subprocess, "run", call)
    monkeypatch.setattr(hook_launcher.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(payload)))
    monkeypatch.setattr(hook_launcher.sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(hook_launcher.sys, "stderr", SimpleNamespace(buffer=errors))
    with pytest.raises(SystemExit) as result:
        hook_launcher.main(["--host", "codex", "--socket", "/private/example.sock"])
    assert result.value.code == 0 and output.getvalue() == b"decision"
    args, kwargs = call.call_args
    assert args[0] == ["/absolute/bundled-client", "hook-worker", "--host", "codex", "--socket", "/private/example.sock"]
    assert kwargs["input"] == payload
    assert kwargs["creationflags"] == (0x08000000 if windows else 0)


def test_real_hook_entry_does_not_import_ordinary_cli_before_the_guard():
    code = "import sys; sys.modules['atellagent_client.cli.main']=None; from atellagent_client.cli.entry import main; main()"
    child = subprocess.run([sys.executable, "-c", code, "hook", "--host", "claude-code",
        "--socket", "/private/tmp/synthetic.sock"], input=b"{}", capture_output=True, timeout=10)
    assert child.returncode == 2 and child.stdout == b""
    assert b"input" in child.stderr.lower() and b"Traceback" not in child.stderr


def test_real_worker_entry_reads_the_host_event_without_recursive_dispatch():
    code = "import sys; sys.modules['atellagent_client.cli.main']=None; from atellagent_client.cli.entry import main; main()"
    child = subprocess.run([sys.executable, "-c", code, "hook-worker", "--host", "claude-code",
        "--socket", "/private/tmp/synthetic.sock"], input=b"{}", capture_output=True, timeout=10)
    assert child.returncode == 2 and child.stdout == b""
    assert b"input" in child.stderr.lower()
