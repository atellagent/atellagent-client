"""Registered launchd jobs can outlive their supervisor process."""
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from atellagent_client.cli import service_platform as platform
from atellagent_client.cli import native_service_controls as controls
from atellagent_client.cli import native_service_status as status_module


@pytest.mark.parametrize('pid', ['-', '0', '-1', 'invalid', 'True'])
def test_registered_job_without_a_live_pid_is_not_running(monkeypatch, pid):
    monkeypatch.setattr(platform.sys, 'platform', 'darwin')
    def launch(*args, **kwargs):
        if args[0] == 'print':
            return subprocess.CompletedProcess(args, 0, 'registered', '')
        assert args == ('list',)
        return subprocess.CompletedProcess(args, 0, f'PID\tStatus\tLabel\n{pid}\t1\t{platform._SUPERVISOR_LABEL}\n', '')
    monkeypatch.setattr(platform, '_launchctl', launch)
    assert platform._supervisor_loaded()
    assert not platform._supervisor_running()


def test_liveness_requires_exact_job_and_positive_pid(monkeypatch):
    monkeypatch.setattr(platform.sys, 'platform', 'darwin')
    values = ['PID\tStatus\tLabel\n123\t0\tcom.other.service\n',
        f'PID\tStatus\tLabel\n456\t0\t{platform._SUPERVISOR_LABEL}\n']
    launch = Mock(side_effect=[subprocess.CompletedProcess(['list'], 0, value, '') for value in values])
    monkeypatch.setattr(platform, '_launchctl', launch)
    assert not platform._supervisor_running()
    assert platform._supervisor_running()
    assert all(call.args == ('list',) and call.kwargs == {'check': False} for call in launch.call_args_list)


@pytest.mark.parametrize('loaded,running', [(False, False), (True, False), (True, True)])
def test_start_bootstraps_or_resumes_registered_job_without_killing_live_owner(tmp_path, monkeypatch, loaded, running):
    monkeypatch.setattr(platform.sys, 'platform', 'darwin')
    monkeypatch.setattr(platform, '_require_supported_platform', Mock())
    path = tmp_path / 'supervisor.plist'
    monkeypatch.setattr(platform, '_validate_supervisor', Mock(return_value=path))
    monkeypatch.setattr(platform, '_supervisor_loaded', lambda: loaded)
    monkeypatch.setattr(platform, '_supervisor_running', lambda: running, raising=False)
    launch = Mock();monkeypatch.setattr(platform, '_launchctl', launch)
    controls.start()
    if running: launch.assert_not_called()
    elif loaded: launch.assert_called_once_with('kickstart', platform._supervisor_target())
    else: launch.assert_called_once_with('bootstrap', f'gui/{platform.os.getuid()}', str(path))


@pytest.mark.asyncio
async def test_device_only_registered_but_exited_service_reports_stopped(tmp_path, monkeypatch):
    service = status_module.service
    monkeypatch.setattr(service, '_require_supported_platform', Mock())
    monkeypatch.setattr(service, '_supervisor_installed', lambda: True)
    monkeypatch.setattr(service, '_validate_supervisor', Mock())
    monkeypatch.setattr(service, '_supervisor_loaded', lambda: True)
    monkeypatch.setattr(service, '_supervisor_running', lambda: False, raising=False)
    monkeypatch.setattr(status_module, 'installation_directory', lambda: tmp_path)
    installation = SimpleNamespace(directory=tmp_path, load=lambda: (SimpleNamespace(tenant_id=None, owner_user_id=None), SimpleNamespace(device_id=None), 'fixture'))
    monkeypatch.setattr(status_module, 'DeviceInstallation', lambda _: installation)
    monkeypatch.setattr(status_module, 'NativeRuntimeSelections', lambda _: SimpleNamespace(load=lambda _: None))
    monkeypatch.setattr(status_module, 'NativeSetupStore', lambda _: None)
    assert await status_module.status() == dict(installed=True, running=False, controllers=[])


def test_uninstall_still_requires_unloading_an_exited_registered_job(tmp_path, monkeypatch):
    monkeypatch.setattr(platform, '_require_supported_platform', Mock())
    monkeypatch.setattr(platform, '_validate_supervisor', Mock(return_value=tmp_path / 'supervisor.plist'))
    monkeypatch.setattr(platform, '_supervisor_loaded', lambda: True)
    monkeypatch.setattr(platform, '_supervisor_running', lambda: False, raising=False)
    with pytest.raises(platform.ServiceCommandError, match='Stop the device service'):
        controls.uninstall()


def test_windows_liveness_keeps_task_scheduler_contract(monkeypatch):
    from atellagent_client.cli import windows_service
    monkeypatch.setattr(platform.sys, 'platform', 'win32')
    command = ['C:/Fixture/atellagent-service.exe']
    monkeypatch.setattr(platform, '_supervisor_command', lambda: command)
    running = Mock(return_value=True);monkeypatch.setattr(windows_service, 'is_running', running)
    assert platform._supervisor_running()
    running.assert_called_once_with('supervisor', command)


@pytest.mark.parametrize('variant', ['missing', 'duplicate', 'extra_fields'])
def test_ambiguous_or_absent_job_rows_never_assert_liveness(monkeypatch, variant):
    monkeypatch.setattr(platform.sys, 'platform', 'darwin')
    row = f"123\t0\t{platform._SUPERVISOR_LABEL}\n"
    table = '' if variant == 'missing' else row * 2 if variant == 'duplicate' else row.strip() + ' unexpected\n'
    monkeypatch.setattr(platform, '_launchctl', Mock(return_value=subprocess.CompletedProcess(['list'], 0, table, '')))
    assert not platform._supervisor_running()


def test_unavailable_process_query_does_not_start_or_assert_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(platform.sys, 'platform', 'darwin')
    monkeypatch.setattr(platform, '_require_supported_platform', Mock())
    monkeypatch.setattr(platform, '_validate_supervisor', Mock(return_value=tmp_path / 'supervisor.plist'))
    monkeypatch.setattr(platform, '_supervisor_loaded', lambda: True)
    launch = Mock(return_value=subprocess.CompletedProcess(['list'], 1, '', 'owned unavailable query'))
    monkeypatch.setattr(platform, '_launchctl', launch)
    with pytest.raises(platform.ServiceCommandError, match='Unable to inspect'):
        controls.start()
    launch.assert_called_once_with('list', check=False)
