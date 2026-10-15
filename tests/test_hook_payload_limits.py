# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Schema-valid bounded file requests must survive local framing unchanged."""

import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from atellagent_client.hook_limits import (
    MAX_REQUEST_BYTES,
    MAX_HOST_INPUT_BYTES,
    MAX_RESPONSE_BYTES,
)

from unittest.mock import patch
from hook_control_support import HookControlRuntimeCase
from atellagent_client.integrations.agents import host_hooks


class HookPayloadLimitRegression(HookControlRuntimeCase):
    async def test_valid_write_above_legacy_64k_reaches_governance_unchanged(self):
        arguments = {"file_path": "/synthetic/fixture.txt", "content": "x" * 70_000}
        result = await self.client.call(
            "action.preflight",
            {
                **self._turn_fields(),
                "tool_call_id": "large-write",
                "tool_name": "Write",
                "arguments": arguments,
                "adapter_version": "atellagent.host-hooks.v1",
                "postflight_required": False,
            },
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(self.governance.preflights[-1].arguments, arguments)
        self.assertEqual(self.governance.action_gate.calls[-1]["facts"], arguments)


class HostInputLimitRegression(unittest.TestCase):
    def test_declared_edit_fields_with_json_escapes_fit_stdin(self):
        value = "\x01" * 2_000_000
        event = {
            "hook_event_name": "PreToolUse",
            "session_id": "synthetic",
            "tool_use_id": "synthetic",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/synthetic/fixture.txt",
                "old_string": value,
                "new_string": value,
                "replace_all": False,
            },
        }
        frame = json.dumps(event, separators=(",", ":")).encode()
        with patch("sys.stdin", new=type("Input", (), {"buffer": io.BytesIO(frame)})()):
            actual = host_hooks._parse_stdin()
        self.assertEqual(actual, event)

    def test_oversized_stdin_fails_without_echoing_contents(self):
        with patch(
            "sys.stdin",
            new=SimpleNamespace(buffer=io.BytesIO(b"x" * (MAX_HOST_INPUT_BYTES + 1))),
        ):
            with self.assertRaisesRegex(ValueError, "^invalid hook input$"):
                host_hooks._parse_stdin()

    def test_shared_launcher_adapter_and_rpc_limits(self):
        from atellagent_client import hook_launcher

        self.assertEqual(hook_launcher._MAX_INPUT_BYTES, MAX_HOST_INPUT_BYTES)
        self.assertEqual(host_hooks._MAX_STDIN_BYTES, MAX_HOST_INPUT_BYTES)
        self.assertEqual(MAX_HOST_INPUT_BYTES, MAX_REQUEST_BYTES)
        self.assertEqual(MAX_RESPONSE_BYTES, 1024 * 1024)


class FullGrammarTransportRegression(HookControlRuntimeCase):
    async def test_maximum_escaped_edit_reaches_gate_with_identical_fingerprint(self):
        value = "\x01" * 2_000_000
        arguments = {
            "file_path": "/synthetic/fixture.txt",
            "old_string": value,
            "new_string": value,
            "replace_all": False,
        }
        result = await self.client.call(
            "action.preflight",
            {
                **self._turn_fields(),
                "tool_call_id": "escaped-edit",
                "tool_name": "Edit",
                "arguments": arguments,
                "adapter_version": "atellagent.host-hooks.v1",
                "postflight_required": False,
            },
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(self.governance.preflights[-1].arguments, arguments)
        self.assertEqual(self.governance.action_gate.calls[-1]["facts"], arguments)
        self.assertFalse(self.governance.outcomes)

    async def test_maximum_patch_reaches_gate_without_operand_rewrite(self):
        prefix = "*** Begin Patch\n*** Add File: /synthetic/fixture.txt\n+"
        suffix = "\n*** End Patch"
        document = prefix + "x" * (1_048_576 - len(prefix) - len(suffix)) + suffix
        arguments = {"command": document}
        result = await self.client.call(
            "action.preflight",
            {
                "host": "codex",
                "session_id": "synthetic",
                "turn_id": "synthetic",
                "tool_call_id": "maximum-patch",
                "tool_name": "apply_patch",
                "host_cwd": "/synthetic",
                "arguments": arguments,
                "adapter_version": "atellagent.host-hooks.v1",
                "postflight_required": False,
            },
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(self.governance.preflights[-1].arguments, arguments)
        self.assertEqual(self.governance.action_gate.calls[-1]["facts"], arguments)

    async def test_oversized_server_frame_never_dispatches(self):
        with patch.object(self.runtime, "_dispatch", new=AsyncMock()) as dispatch:
            response = await self.runtime._response_for(b"x" * (MAX_REQUEST_BYTES + 1))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], {"code": "invalid_request"})
        dispatch.assert_not_awaited()


class LauncherPayloadRegression(unittest.TestCase):
    def test_real_child_receives_large_escaped_write_byte_identically(self):
        import hashlib
        import os
        import tempfile
        from pathlib import Path
        from atellagent_client import hook_launcher

        event = {
            "hook_event_name": "PreToolUse",
            "session_id": "synthetic",
            "tool_use_id": "synthetic",
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/synthetic/fixture.txt",
                "content": "\x01" * 2_000_000,
            },
        }
        frame = json.dumps(event, separators=(",", ":")).encode()
        with tempfile.TemporaryDirectory() as work:
            Path(work, "controlled_adapter.py").write_text(
                "def main():\n import sys,hashlib\n print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())\n"
            )
            output, error = io.BytesIO(), io.BytesIO()
            with patch.object(
                hook_launcher, "_ADAPTER_MODULE", "controlled_adapter"
            ), patch.dict(os.environ, {"PYTHONPATH": work}), patch(
                "sys.stdin", new=SimpleNamespace(buffer=io.BytesIO(frame))
            ), patch(
                "sys.stdout", new=SimpleNamespace(buffer=output)
            ), patch(
                "sys.stderr", new=io.TextIOWrapper(error, write_through=True)
            ):
                with self.assertRaises(SystemExit) as result:
                    hook_launcher.main(
                        ["--host", "claude-code", "--socket", "/synthetic/control.sock"]
                    )
                self.assertEqual(result.exception.code, 0)
                self.assertEqual(
                    output.getvalue().decode().strip(),
                    hashlib.sha256(frame).hexdigest(),
                )
                self.assertEqual(error.getvalue(), b"")


class WindowsBufferContract(unittest.TestCase):
    def test_pipe_allocation_uses_shared_caps_and_preserves_owner_only_flags(self):
        from unittest.mock import MagicMock
        from atellagent_client.integrations.agents import windows_named_pipe as pipe

        winpipe = SimpleNamespace(
            PIPE_ACCESS_DUPLEX=3,
            PIPE_TYPE_MESSAGE=4,
            PIPE_READMODE_MESSAGE=2,
            PIPE_WAIT=0,
            CreateNamedPipe=MagicMock(return_value="handle"),
        )
        attrs = object()
        modules = (object(), object(), object(), object(), winpipe, object(), object())
        server = pipe.WindowsNamedPipeServer(
            r"\\.\pipe\synthetic", lambda frame: None, handler_timeout_seconds=1
        )
        with patch.object(pipe, "_windows_modules", return_value=modules), patch.object(
            pipe, "_current_user_security_descriptor", return_value=(object(), attrs)
        ):
            self.assertEqual(server._new_pipe(first_instance=True), "handle")
        args = winpipe.CreateNamedPipe.call_args.args
        self.assertEqual(args[3], 1)
        self.assertEqual(args[4:6], (MAX_RESPONSE_BYTES + 1, MAX_REQUEST_BYTES + 1))
        self.assertIs(args[-1], attrs)
        self.assertTrue(args[1] & pipe._FILE_FLAG_FIRST_PIPE_INSTANCE)
        self.assertTrue(args[2] & pipe._PIPE_REJECT_REMOTE_CLIENTS)
