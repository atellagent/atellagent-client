"""Real shell quoting/stdio checks using an isolated non-network child fixture."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from atellagent_client.cli.native_hook_commands import command


class NativeHookShellTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "requires actual Windows command shells")
    def test_missing_windows_executable_is_blocking_not_powershell_success(self):
        with tempfile.TemporaryDirectory(prefix="Atellagent missing hook ") as temporary:
            for host in ("codex", "claude-code"):
                with self.subTest(host=host):
                    args = [str(Path(temporary) / "missing.exe"), "hook", "--host", host,
                        "--socket", rf"\\.\pipe\atellagent-test-{host}"]
                    handler = command(args, platform="win32", host=host)
                    shell = (["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", handler["command"]]
                        if host == "claude-code" else ["cmd.exe", "/d", "/s", "/c", handler["command"]])
                    result = subprocess.run(shell, input=b"{}", capture_output=True,
                        timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, b"")
                    self.assertIn(b"request denied", result.stderr)

    @unittest.skipUnless(sys.platform == "win32", "requires actual Windows command shells")
    def test_windows_commands_deliver_stdin_and_preserve_blocking_exit(self):
        with tempfile.TemporaryDirectory(prefix="Atellagent hook shell ") as temporary:
            root = Path(temporary)
            # Python stands in for the installed CLI: argv[1] selects this local fixture.
            (root / "hook").write_text("import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\nsys.exit(2)\n")
            payload = b'{"prompt":"caf\xc3\xa9","hook_event_name":"PreToolUse"}'
            for host in ("codex", "claude-code"):
                with self.subTest(host=host):
                    args = [sys.executable, "hook", "--host", host, "--socket", rf"\\.\pipe\atellagent-test-{host}"]
                    handler = command(args, platform="win32", host=host)
                    shell = (["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", handler["command"]]
                        if host == "claude-code" else ["cmd.exe", "/d", "/s", "/c", handler["command"]])
                    result = subprocess.run(shell, cwd=root, input=payload, capture_output=True,
                        timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                    self.assertEqual(result.returncode, 2, result.stderr.decode(errors="replace"))
                    self.assertEqual(result.stdout.strip(), payload)


if __name__ == "__main__":
    unittest.main()
