"""OS service actions do not import configs, log in or implicitly restart."""

from unittest.mock import Mock, AsyncMock

import pytest

from atellagent_client.cli import native_service_controls as module
from atellagent_client.cli import native_service


@pytest.mark.parametrize("running", [False, True])
def test_start_targets_validated_shared_service_without_stop(monkeypatch, tmp_path, running):
    platform = module.service
    monkeypatch.setattr(platform, "_require_supported_platform", Mock())
    monkeypatch.setattr(platform, "_validate_supervisor", Mock(return_value=tmp_path / "service.plist"))
    monkeypatch.setattr(platform, "_supervisor_loaded", lambda: running)
    monkeypatch.setattr(platform, "_supervisor_running", lambda: running)
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    launch = Mock()
    monkeypatch.setattr(platform, "_launchctl", launch)
    module.start()
    if running:
        launch.assert_not_called()
    else:
        launch.assert_called_once_with("bootstrap", f"gui/{platform.os.getuid()}", str(tmp_path / "service.plist"))


def test_uninstall_refuses_running_service_without_stopping(monkeypatch, tmp_path):
    monkeypatch.setattr(module.service, "_require_supported_platform", Mock())
    monkeypatch.setattr(module.service, "_validate_supervisor", Mock(return_value=tmp_path / "service.plist"))
    monkeypatch.setattr(module.service, "_supervisor_loaded", lambda: True)
    with pytest.raises(module.service.ServiceCommandError, match="Stop the device service"):
        module.uninstall()


@pytest.mark.asyncio
async def test_start_requires_retained_device_before_os_action(monkeypatch):
    from atellagent_client.onboarding.errors import OnboardingClientError
    monkeypatch.setattr(native_service.DeviceInstallation, "load",
                        Mock(side_effect=OnboardingClientError("device_installation_missing")))
    start = Mock()
    monkeypatch.setattr(native_service.controls, "start", start)
    with pytest.raises(SystemExit):
        await native_service.run(["start"])
    start.assert_not_called()


@pytest.mark.asyncio
async def test_start_does_not_wait_for_remote_admission_or_controller_readiness(monkeypatch):
    retained = Mock()
    monkeypatch.setattr(native_service.DeviceInstallation, "load", retained)
    remote = AsyncMock(side_effect=AssertionError("Start must not perform online admission"))
    monkeypatch.setattr(native_service.NativeSetupContext, "open", remote)
    start = Mock()
    monkeypatch.setattr(native_service.controls, "start", start)
    await native_service.run(["start"])
    retained.assert_called_once()
    start.assert_called_once()
    remote.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [["login"], ["install", "--config", "old.yaml"], ["start", "codex"]])
async def test_retired_service_inputs_are_rejected(arguments):
    with pytest.raises(SystemExit):
        await native_service.run(arguments)


def test_windows_start_uses_windowless_shared_task_without_restart(monkeypatch, tmp_path):
    from atellagent_client.cli import windows_service
    platform = module.service
    monkeypatch.setattr(platform, "_require_supported_platform", Mock())
    monkeypatch.setattr(platform, "_validate_supervisor", Mock(return_value=tmp_path / "supervisor.task"))
    monkeypatch.setattr(platform, "_supervisor_loaded", lambda: False)
    monkeypatch.setattr(platform.sys, "platform", "win32")
    command = ["C:/Atellagent/atellagent-service.exe"]
    monkeypatch.setattr(platform, "_supervisor_command", lambda: command)
    start, stop = Mock(), Mock()
    monkeypatch.setattr(windows_service, "start_task", start)
    monkeypatch.setattr(windows_service, "stop_task", stop)
    module.start()
    start.assert_called_once_with("supervisor", command)
    stop.assert_not_called()
