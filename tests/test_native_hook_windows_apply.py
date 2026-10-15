"""Real Windows settings writes using disposable profiles and generated changes."""

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from atellagent_client.cli.native_hook_definition import definition
from atellagent_client.cli.native_hook_review import prepare
from atellagent_client.cli.native_hook_windows_directory import protect_created
from atellagent_client.cli.native_hook_windows_apply import apply, ApplyError
from atellagent_client.cli.native_hook_snapshot import snapshot
from atellagent_client.onboarding.errors import OnboardingClientError


@unittest.skipUnless(os.name == "nt", "actual Windows settings replacement")
class WindowsApplyTests(unittest.TestCase):
    def test_system_owner_is_allowed_only_for_profile_root(self):
        import win32security as security
        from atellagent_client.cli.native_hook_windows_directory import pinned_directory
        original = security.GetSecurityInfo

        def system_owned(*args):
            descriptor = original(*args)
            descriptor.SetSecurityDescriptorOwner(security.ConvertStringSidToSid("S-1-5-18"), False)
            return descriptor

        with tempfile.TemporaryDirectory(prefix="Atellagent SYSTEM profile ") as temporary:
            home = Path(temporary)
            protect_created(home)
            with patch.object(security, "GetSecurityInfo", side_effect=system_owned):
                # Exercises the ancestry pinning path as well as snapshot reads.
                with pinned_directory(home, home):
                    pass
                self.assertFalse(snapshot(home, home / ".claude" / "settings.json").exists)
                directory = home / ".claude"
                directory.mkdir()
                protect_created(directory)
                with self.assertRaises(OnboardingClientError):
                    snapshot(home, directory / "settings.json")

    def test_reviewed_removal_preserves_unrelated_settings_for_both_hosts(self):
        for host in ("codex", "claude-code"):
            with self.subTest(host=host), tempfile.TemporaryDirectory(prefix="Atellagent removal ") as temporary:
                home = Path(temporary)
                protect_created(home)
                draft = SimpleNamespace(proposal=SimpleNamespace(id=uuid4(), native_host=host), device_id=uuid4())
                proposed = definition(draft=draft, executable=r"C:\Applications\Atellagent\cli.exe",
                    endpoint=rf"\\.\pipe\atellagent-test-{host}", home=str(home), platform="win32")
                directory = home / (".codex" if host == "codex" else ".claude")
                directory.mkdir(); protect_created(directory)
                path = directory / ("hooks.json" if host == "codex" else "settings.json")
                path.write_bytes(b'{"env":{"CUSTOMER":"retained"}}'); protect_created(path)
                installation = prepare(proposed, home=home)
                apply(installation, review_hash=installation.public["review_sha256"], require_selection=Mock())
                removal = prepare(proposed, home=home, remove=True)
                apply(removal, review_hash=removal.public["review_sha256"], require_selection=Mock())
                self.assertEqual(json.loads(path.read_bytes()), {"env": {"CUSTOMER": "retained"}, "hooks": {}})
                repeat = prepare(proposed, home=home, remove=True)
                self.assertEqual(apply(repeat, review_hash=repeat.public["review_sha256"], require_selection=Mock())["written"], [])

    def proposed(self, home):
        draft = SimpleNamespace(proposal=SimpleNamespace(id=uuid4(), native_host="codex"), device_id=uuid4())
        return definition(draft=draft, executable=r"C:\Applications\Atellagent\cli.exe",
            endpoint=r"\\.\pipe\atellagent-test-codex", home=str(home), platform="win32")

    def test_apply_creates_private_settings_and_repeated_review_is_noop(self):
        with tempfile.TemporaryDirectory(prefix="Atellagent apply ") as temporary:
            home = Path(temporary)
            protect_created(home)
            proposed = self.proposed(home)
            reviewed = prepare(proposed, home=home)
            result = apply(reviewed, review_hash=reviewed.public["review_sha256"], require_selection=Mock())
            self.assertEqual(result["settings"], "saved")
            self.assertEqual(result["readiness"], "not_verified")
            self.assertEqual(len(result["written"]), 1)
            again = prepare(proposed, home=home)
            self.assertEqual(apply(again, review_hash=again.public["review_sha256"], require_selection=Mock())["written"], [])
            self.assertFalse(list((home / ".codex").glob(".atellagent-hook-*")))

    def test_user_edit_and_selection_loss_do_not_overwrite(self):
        with tempfile.TemporaryDirectory(prefix="Atellagent apply ") as temporary:
            home = Path(temporary)
            protect_created(home)
            directory = home / ".codex"
            directory.mkdir()
            protect_created(directory)
            path = directory / "hooks.json"
            path.write_bytes(b'{"env":{"SECRET":"retain"}}')
            protect_created(path)
            proposed = self.proposed(home)
            reviewed = prepare(proposed, home=home)
            path.write_bytes(b'{"env":{"SECRET":"edited"}}')
            with self.assertRaises(ApplyError) as error:
                apply(reviewed, review_hash=reviewed.public["review_sha256"], require_selection=Mock())
            self.assertEqual(error.exception.written, ())
            fresh = prepare(proposed, home=home)
            selection = Mock(side_effect=[None, OnboardingClientError("onboarding_native_selection_changed")])
            with self.assertRaises(ApplyError):
                apply(fresh, review_hash=fresh.public["review_sha256"], require_selection=selection)
            self.assertEqual(json.loads(path.read_bytes())["env"]["SECRET"], "edited")
            self.assertFalse(list(directory.glob(".atellagent-hook-*")))

    def test_existing_values_and_reviewed_acl_are_preserved(self):
        import win32security as security
        import win32con
        with tempfile.TemporaryDirectory(prefix="Atellagent ACL apply ") as temporary:
            home = Path(temporary)
            protect_created(home)
            directory = home / ".codex"
            directory.mkdir()
            protect_created(directory)
            path = directory / "hooks.json"
            path.write_bytes(b'{"env":{"SECRET":"retain"}}')
            protect_created(path)
            descriptor = security.GetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
                security.DACL_SECURITY_INFORMATION)
            acl = descriptor.GetSecurityDescriptorDacl()
            acl.AddAccessAllowedAce(security.ACL_REVISION, win32con.GENERIC_READ,
                security.CreateWellKnownSid(security.WinWorldSid, None))
            security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
                security.DACL_SECURITY_INFORMATION, None, None, acl, None)
            before = snapshot(home, path)
            reviewed = prepare(self.proposed(home), home=home)
            apply(reviewed, review_hash=reviewed.public["review_sha256"], require_selection=Mock())
            after = snapshot(home, path)
            self.assertEqual(after.security, before.security)
            self.assertEqual(json.loads(after.content)["env"]["SECRET"], "retain")


if __name__ == "__main__":
    unittest.main()
