"""OS contracts preserved from the retired config-driven service suite."""

from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from atellagent_client.cli import service_platform as service, windows_service


class ServicePlatformTests(unittest.TestCase):
    def test_mac_service_restarts_failures_with_throttling_not_clean_exits(self) -> None:
        command = ["/Applications/Atellagent.app/Contents/Resources/runtime/atellagent-cli/atellagent-cli", "service", "run"]
        with patch.object(service, "_supervisor_command", return_value=command):
            definition = plistlib.loads(service._supervisor_agent_data(Path("/private/service-state")))
        self.assertEqual(definition["KeepAlive"], {"SuccessfulExit": False})
        self.assertEqual(definition["ThrottleInterval"], 30)
        self.assertTrue(definition["RunAtLoad"])
        self.assertEqual(definition["ProgramArguments"], command)

    def test_launchctl_timeout_reports_uncertain_state_instead_of_success(self) -> None:
        with patch.object(service.subprocess, "run", side_effect=subprocess.TimeoutExpired("launchctl", 15)) as run:
            with self.assertRaisesRegex(service.ServiceCommandError, "timed out; refresh local service status"):
                service._launchctl("bootout", "gui/501/com.atellagent.hook-control")
        self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_wait_for_unloaded_polls_until_launchd_removes_label(self) -> None:
        responses = [Mock(returncode=0), Mock(returncode=0), Mock(returncode=113)]
        with patch.object(service, "_launchctl", side_effect=responses) as launchctl, patch.object(
            service, "sleep"
        ) as sleep:
            service._wait_for_unloaded("gui/501/com.atellagent.hook-control")
        self.assertEqual(launchctl.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_windows_task_uses_interactive_user_token_without_password_or_elevation(self) -> None:
        class Record:
            pass

        class Collection:
            def __init__(self) -> None:
                self.kind = None
                self.item = Record()

            def Create(self, kind: int) -> Record:
                self.kind = kind
                return self.item

        definition = Record()
        definition.RegistrationInfo = Record()
        definition.Settings = Record()
        definition.Principal = Record()
        definition.Triggers = Collection()
        definition.Actions = Collection()
        scheduler = Record()
        scheduler.NewTask = lambda _flags: definition
        folder = Record()
        registered: list[tuple[object, ...]] = []
        folder.RegisterTaskDefinition = lambda *args: registered.append(args)
        command = [r"C:\Users\Chris\App Data\atellagent-service.exe"]
        with patch.object(windows_service, "_scheduler", return_value=(scheduler, folder)), patch.object(
            windows_service, "_existing_task", return_value=None
        ), patch.object(windows_service, "current_user_sid", return_value="S-1-5-21-123"):
            windows_service.register_task("supervisor", command)

        self.assertEqual("S-1-5-21-123", definition.Principal.UserId)
        self.assertEqual(3, definition.Principal.LogonType)
        self.assertEqual(0, definition.Principal.RunLevel)
        self.assertEqual(3, definition.Settings.RestartCount)
        self.assertEqual("PT1M", definition.Settings.RestartInterval)
        self.assertEqual(2, definition.Settings.MultipleInstances)
        self.assertEqual(9, definition.Triggers.kind)
        self.assertEqual("S-1-5-21-123", definition.Triggers.item.UserId)
        self.assertEqual(0, definition.Actions.kind)
        self.assertEqual(command[0], definition.Actions.item.Path)
        self.assertEqual(subprocess.list2cmdline(command[1:]), definition.Actions.item.Arguments)
        self.assertEqual((2, "S-1-5-21-123", None, 3), registered[0][2:])

    def test_frozen_windows_supervisor_uses_windowless_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            cli = runtime / "atellagent-cli" / "atellagent-cli.exe"
            runner = runtime / "atellagent-service" / "atellagent-service.exe"
            runner.parent.mkdir(parents=True)
            runner.write_bytes(b"")
            with patch.object(service.sys, "platform", "win32"), patch.object(
                service.sys, "executable", str(cli)
            ), patch.object(service.sys, "frozen", True, create=True):
                self.assertEqual([str(runner)], service._supervisor_command())

    def test_windows_missing_task_wrapped_com_error_is_not_a_failure(self) -> None:
        missing = OSError("Task Scheduler task not found")
        missing.hresult = -2147352567
        missing.excepinfo = (0, None, None, None, 0, -2147024894)
        folder = type("Folder", (), {"GetTask": lambda self, _name: (_ for _ in ()).throw(missing)})()

        self.assertIsNone(windows_service._existing_task(folder, "AtellagentHookControl-test-codex"))

    def test_windows_task_principal_resolves_to_current_user_sid(self) -> None:
        fake_security = SimpleNamespace(
            LookupAccountName=lambda _system, name: ("resolved-sid" if name == "chris" else "other-sid", "machine", 1),
            ConvertSidToStringSid=lambda sid: "S-1-5-21-123" if sid == "resolved-sid" else "other",
        )
        with patch.object(windows_service, "current_user_sid", return_value="S-1-5-21-123"), patch.dict(
            sys.modules, {"win32security": fake_security}
        ):
            self.assertTrue(windows_service._principal_is_current_user("chris"))
            self.assertTrue(windows_service._principal_is_current_user("S-1-5-21-123"))
            self.assertFalse(windows_service._principal_is_current_user("other"))
