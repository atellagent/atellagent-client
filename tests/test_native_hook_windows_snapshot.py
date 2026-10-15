"""Real Win32 snapshot checks; all files are disposable, not active settings."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from atellagent_client.cli.native_hook_snapshot import snapshot, require_unchanged, LIMIT
from atellagent_client.onboarding.errors import OnboardingClientError


@unittest.skipUnless(os.name == "nt", "actual Windows handles required")
class WindowsSnapshotTests(unittest.TestCase):
    def owned(self, path):
        # SSH may run elevated and create Administrator-owned fixtures. Model
        # the desktop user's files explicitly, without weakening the reader.
        import win32api
        import win32con
        import win32security as security
        token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            current = security.GetTokenInformation(token, security.TokenUser)[0]
        finally:
            token.Close()
        security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
            security.OWNER_SECURITY_INFORMATION, current, None, None, None)
        return path

    def test_capture_edit_and_missing_paths(self):
        with tempfile.TemporaryDirectory(prefix="Atellagent snapshot ") as temporary:
            root = self.owned(Path(temporary))
            path = root / ".codex" / "hooks.json"
            missing = snapshot(root, path)
            self.assertFalse(missing.exists)
            self.assertFalse(path.parent.exists())
            path.parent.mkdir()
            self.owned(path.parent)
            path.write_bytes(b'{"secret":"hidden"}')
            self.owned(path)
            saved = snapshot(root, path)
            self.assertNotIn("hidden", repr(saved))
            require_unchanged(root, path, saved)
            path.write_bytes(b"{}")
            with self.assertRaises(OnboardingClientError):
                require_unchanged(root, path, saved)

    def test_junction_hardlink_oversized_and_alternate_stream_rejected(self):
        with tempfile.TemporaryDirectory(prefix="Atellagent snapshot ") as temporary:
            root = self.owned(Path(temporary))
            real = root / "real"
            real.mkdir()
            self.owned(real)
            junction = root / "junction"
            result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(real)],
                capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            with self.assertRaises(OnboardingClientError): snapshot(root, junction / "hooks.json")
            junction.rmdir()
            path = real / "hooks.json"
            path.write_bytes(b"{}")
            self.owned(path)
            link = real / "copy"
            os.link(path, link)
            with self.assertRaises(OnboardingClientError): snapshot(root, path)
            link.unlink()
            with self.assertRaises(OnboardingClientError): snapshot(root, real / "hooks.json:alternate")
            path.write_bytes(b"x" * (LIMIT + 1))
            with self.assertRaises(OnboardingClientError): snapshot(root, path)

    def test_dacl_changes_invalidate_review_and_shared_writes_are_rejected(self):
        import win32con
        import win32security as security
        with tempfile.TemporaryDirectory(prefix="Atellagent ACL snapshot ") as temporary:
            root = self.owned(Path(temporary))
            path = root / "hooks.json"
            path.write_bytes(b"{}")
            self.owned(path)
            saved = snapshot(root, path)
            descriptor = security.GetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
                security.DACL_SECURITY_INFORMATION)
            acl = descriptor.GetSecurityDescriptorDacl()
            everyone = security.CreateWellKnownSid(security.WinWorldSid, None)
            acl.AddAccessAllowedAce(security.ACL_REVISION, win32con.GENERIC_READ, everyone)
            security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
                security.DACL_SECURITY_INFORMATION, None, None, acl, None)
            self.assertNotEqual(snapshot(root, path).revision, saved.revision)
            acl.AddAccessAllowedAce(security.ACL_REVISION, win32con.GENERIC_WRITE, everyone)
            security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
                security.DACL_SECURITY_INFORMATION, None, None, acl, None)
            with self.assertRaises(OnboardingClientError): snapshot(root, path)

    def test_write_sharing_is_blocked_while_file_is_captured(self):
        from unittest.mock import patch
        import win32file
        with tempfile.TemporaryDirectory(prefix="Atellagent locked snapshot ") as temporary:
            root = self.owned(Path(temporary))
            path = root / "hooks.json"
            path.write_bytes(b"{}")
            self.owned(path)
            original = win32file.ReadFile
            attempts = []
            def reading(*args):
                with self.assertRaises(OSError): path.write_bytes(b"changed")
                attempts.append(True)
                return original(*args)
            with patch.object(win32file, "ReadFile", reading):
                captured = snapshot(root, path)
            self.assertTrue(attempts)
            self.assertEqual(captured.content, b"{}")


if __name__ == "__main__":
    unittest.main()
