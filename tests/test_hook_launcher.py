"""A real failing adapter process must produce a blocking hook result."""
import io
import json
import subprocess
import sys
from textwrap import indent
from types import SimpleNamespace
import pytest
from atellagent_client import hook_launcher as launcher


def run_launcher(monkeypatch, tmp_path, code, *, host="claude-code", event=None):
    (tmp_path / "controlled_adapter.py").write_text("def main():\n" + indent(code, "    "))
    payload = json.dumps(event or {"hook_event_name":"PreToolUse", "tool_name":"Read", "tool_input":{},
                                "session_id":"synthetic", "tool_use_id":"synthetic-call"}).encode()
    out, err = io.BytesIO(), io.BytesIO()
    stderr = io.TextIOWrapper(err, write_through=True)
    with monkeypatch.context() as m:
        m.setattr(launcher, "_ADAPTER_MODULE", "controlled_adapter")
        m.setenv("PYTHONPATH", str(tmp_path))
        m.setattr(launcher.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(payload)))
        m.setattr(launcher.sys, "stdout", SimpleNamespace(buffer=out))
        m.setattr(launcher.sys, "stderr", stderr)
        with pytest.raises(SystemExit) as exit_info:
            launcher.main(["--host",host,"--socket","/private/tmp/synthetic.sock"])
    return exit_info.value.code, out.getvalue(), err.getvalue(), payload


@pytest.mark.parametrize("host", ["codex","claude-code","gemini-cli"])
def test_real_child_syntax_failure_blocks_without_leaking_traceback(monkeypatch,tmp_path,host):
    code,out,err,_ = run_launcher(monkeypatch,tmp_path,"if True:\n",host=host)
    assert code == 2 and out == b""
    assert err.decode().strip() == launcher._FAILURE
    assert b"SyntaxError" not in err


@pytest.mark.parametrize("status", ["blocked","paused","complete"])
def test_exact_codex_goal_recovers_on_child_startup_failure(monkeypatch,tmp_path,status):
    event={"hook_event_name":"PreToolUse","tool_name":"update_goal","tool_input":{"status":status},
           "session_id":"synthetic","tool_use_id":"synthetic-call"}
    code,out,err,_=run_launcher(monkeypatch,tmp_path,"if True:\n",host="codex",event=event)
    assert (code,out,err)==(0,b"",b"")


@pytest.mark.parametrize("changes", [
    {"tool_input":{"status":"active"}}, {"tool_input":{"status":"blocked","extra":True}},
    {"tool_use_id":None}, {"hook_event_name":{}}, {"hook_event_name":[]}, {"session_id":""},
])
def test_malformed_goal_never_escapes_failed_child(monkeypatch,tmp_path,changes):
    event={"hook_event_name":"PreToolUse","tool_name":"update_goal","tool_input":{"status":"blocked"},
           "session_id":"synthetic","tool_use_id":"synthetic-call",**changes}
    assert run_launcher(monkeypatch,tmp_path,"if True:\n",host="codex",event=event)[0]==2


def test_healthy_goal_policy_deny_is_preserved(monkeypatch,tmp_path):
    event={"hook_event_name":"PreToolUse","tool_name":"update_goal","tool_input":{"status":"complete"},
           "session_id":"synthetic","tool_use_id":"synthetic-call"}
    expected=b'{"hookSpecificOutput":{"permissionDecision":"deny"}}'
    code,out,err,_=run_launcher(monkeypatch,tmp_path,
        "import sys\nsys.stdout.buffer.write("+repr(expected)+")\n",host="codex",event=event)
    assert (code,out,err)==(0,expected,b"")


def test_healthy_adapter_receives_exact_input_bytes(monkeypatch,tmp_path):
    code,out,err,payload=run_launcher(monkeypatch,tmp_path,"import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n")
    assert (code,out,err)==(0,payload,b"")


def test_documented_blocking_auth_reason_is_preserved(monkeypatch,tmp_path):
    message=b'Atellagent sign-in is required; request denied.\n'
    code,out,err,_=run_launcher(monkeypatch,tmp_path,
        "import sys\nsys.stderr.buffer.write("+repr(message)+")\nraise SystemExit(2)\n")
    assert (code,out,err)==(2,b"",message)


def test_private_unexpected_child_failure_is_not_exposed(monkeypatch,tmp_path):
    code,out,err,_=run_launcher(monkeypatch,tmp_path,"print('synthetic-private-value')\nraise RuntimeError('synthetic-secret')\n")
    assert code==2 and out==b"" and b"synthetic-secret" not in err and b"synthetic-private-value" not in err


@pytest.mark.parametrize("goal", [False, True])
def test_real_child_timeout_blocks_tools_but_releases_exact_goal(monkeypatch,tmp_path,goal):
    monkeypatch.setattr(launcher,"_DEADLINE_SECONDS",0.05)
    event={"hook_event_name":"PreToolUse","tool_name":"update_goal" if goal else "Read",
           "tool_input":{"status":"blocked"} if goal else {},
           "session_id":"synthetic","tool_use_id":"synthetic-call"}
    code,out,err,_=run_launcher(monkeypatch,tmp_path,"import time\ntime.sleep(30)\n",host="codex",event=event)
    assert code==(0 if goal else 2) and out==b""
    assert err==(b"" if goal else (launcher._PRETOOL_TIMEOUT_FAILURE+"\n").encode())


def test_claude_never_uses_codex_goal_recovery(monkeypatch,tmp_path):
    event={"hook_event_name":"PreToolUse","tool_name":"update_goal","tool_input":{"status":"blocked"},
           "session_id":"synthetic","tool_use_id":"synthetic-call"}
    assert run_launcher(monkeypatch,tmp_path,"if True:\n",event=event)[0]==2


def test_deep_invalid_goal_json_is_not_an_unhandled_launcher_error():
    assert launcher._goal_recovery("codex",b"["*2000+b"]"*2000) is False


def test_launcher_input_bound_matches_ordinary_adapter():
    from atellagent_client.integrations.agents import host_hooks
    assert launcher._MAX_INPUT_BYTES==host_hooks._MAX_STDIN_BYTES


def test_real_launcher_does_not_reexecute_preimported_adapter():
    child = subprocess.run(
        [sys.executable, "-m", "atellagent_client.hook_launcher",
         "--host", "claude-code", "--socket", "/private/tmp/synthetic.sock"],
        input=b"{}", capture_output=True, timeout=5, check=False,
    )
    assert child.returncode == 2
    assert child.stdout == b""
    assert b"RuntimeWarning" not in child.stderr
    assert b"input" in child.stderr.lower()


@pytest.mark.parametrize("status", ["blocked", "paused", "complete"])
@pytest.mark.parametrize("checkpoint", ["PreToolUse", "PostToolUse"])
@pytest.mark.parametrize("reason", ["policy_denied", "control_directive_rejected"])
def test_exact_goal_preserves_documented_worker_block(monkeypatch, tmp_path, status, checkpoint, reason):
    event = {"hook_event_name": checkpoint, "tool_name": "update_goal",
             "tool_input": {"status": status}, "session_id": "synthetic",
             "tool_use_id": "synthetic-call"}
    message = ("synthetic blocking reason: " + reason + "\n").encode()
    code, out, err, _ = run_launcher(
        monkeypatch, tmp_path,
        "import sys\nsys.stdout.write('discard partial output')\n"
        "sys.stderr.buffer.write(" + repr(message) + ")\nraise SystemExit(2)\n",
        host="codex", event=event,
    )
    assert (code, out, err) == (2, b"", message)


@pytest.mark.parametrize("host,checkpoint", [
    ("codex", "PreToolUse"), ("claude-code", "PreToolUse"),
    ("gemini-cli", "BeforeTool"),
])
def test_real_worker_deadline_explains_pretool_denial_without_partial_output(monkeypatch, tmp_path, host, checkpoint):
    monkeypatch.setattr(launcher, "_DEADLINE_SECONDS", 0.1)
    event = {"hook_event_name": checkpoint, "tool_name": "Read", "tool_input": {},
             "session_id": "synthetic", "tool_use_id": "synthetic-call"}
    code, out, err, _ = run_launcher(monkeypatch, tmp_path,
        "import sys, time\nsys.stdout.write('private-partial-allow')\nsys.stdout.flush()\n"
        "sys.stderr.write('private-worker-detail')\nsys.stderr.flush()\ntime.sleep(30)\n",
        host=host, event=event)
    assert code == 2 and out == b""
    assert b"preflight timed out" in err and b"tool was not executed" in err
    assert b"unchanged tool call once" in err
    assert b"private" not in err and b"policy denied" not in err


@pytest.mark.parametrize("host,checkpoint", [
    ("codex", "PostToolUse"), ("claude-code", "PostToolUse"),
    ("claude-code", "PostToolUseFailure"),
])
def test_real_postflight_deadline_does_not_instruct_reexecution(monkeypatch, tmp_path, host, checkpoint):
    monkeypatch.setattr(launcher, "_DEADLINE_SECONDS", 0.1)
    event = {"hook_event_name": checkpoint, "tool_name": "Read", "tool_input": {},
             "session_id": "synthetic", "tool_use_id": "synthetic-call"}
    code, out, err, _ = run_launcher(monkeypatch, tmp_path, "import time\ntime.sleep(30)\n", host=host, event=event)
    assert code == 2 and out == b""
    assert b"post-tool reporting timed out" in err and b"may already have executed" in err
    assert b"Do not repeat the tool" in err and b"tool was not executed" not in err


@pytest.mark.parametrize("status", ["blocked", "paused", "complete"])
@pytest.mark.parametrize("checkpoint", ["PreToolUse", "PostToolUse"])
def test_exact_terminal_goal_still_recovers_on_real_worker_deadline(monkeypatch, tmp_path, status, checkpoint):
    monkeypatch.setattr(launcher, "_DEADLINE_SECONDS", 0.1)
    event = {"hook_event_name": checkpoint, "tool_name": "update_goal", "tool_input": {"status": status},
             "session_id": "synthetic", "tool_use_id": "synthetic-call"}
    code, out, err, _ = run_launcher(monkeypatch, tmp_path, "import time\ntime.sleep(30)\n", host="codex", event=event)
    assert (code, out, err) == (0, b"", b"")


@pytest.mark.parametrize("payload", [b"{}", b"[]", b"\xff", b"[" * 2000 + b"]" * 2000,
                                     b'{"hook_event_name": {"PreToolUse": "private"}}'])
def test_timeout_message_handles_invalid_input_without_exposing_it(payload):
    assert launcher._timeout_failure(payload) == "Atellagent hook adapter timed out; request denied."


def test_prompt_timeout_is_not_described_as_a_tool_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "_DEADLINE_SECONDS", 0.1)
    code, out, err, _ = run_launcher(monkeypatch, tmp_path, "import time\ntime.sleep(30)\n",
        event={"hook_event_name": "UserPromptSubmit", "prompt": "private prompt", "session_id": "synthetic"})
    assert code == 2 and out == b""
    assert err.decode().strip() == "Atellagent hook adapter timed out; request denied."
