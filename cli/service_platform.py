# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Current-user OS service registration and endpoints; no onboarding adapters."""

from hashlib import sha256
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from time import monotonic, sleep

from .service_errors import ServiceCommandError
from .service_files import _owned_directory, _read_owned_file, _write_private_file
from .windows_service import WindowsServiceError
from atellagent_client.onboarding.native_runtime_selection import HOSTS as _HOSTS
from atellagent_client.integrations.agents.hook_control_protocol import HookControlClient, HookControlError

_LABEL_PREFIX = "com.atellagent.hook-control"
_SUPERVISOR_LABEL = _LABEL_PREFIX


def _require_supported_platform() -> None:
    if sys.platform not in {"darwin", "win32"}:
        raise ServiceCommandError("Per-user service commands currently support macOS and Windows")
    if sys.platform == "darwin" and os.getuid() == 0:
        raise ServiceCommandError("Install the service from the signed-in user account, not as root")

def _service_directory() -> Path:
    _require_supported_platform()
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise ServiceCommandError("Windows user profile storage is unavailable")
        return Path(local_app_data) / "Atellagent" / "services"
    return Path.home() / "Library" / "Application Support" / "Atellagent" / "services"

def _endpoint(host: str) -> str:
    # Endpoint selection also serves the Linux foreground supervisor; OS service
    # registration remains a macOS/Windows-only operation.
    if sys.platform not in {"darwin", "win32", "linux"}:
        raise ServiceCommandError("Local controller endpoints support macOS, Windows and Linux")
    if host not in _HOSTS:
        raise ServiceCommandError("Unsupported coding host")
    if sys.platform == "win32":
        from atellagent_client.cli.windows_service import current_user_sid
        user_suffix = sha256(current_user_sid().encode("utf-8")).hexdigest()[:12]
        return f"\\\\.\\pipe\\atellagent-{user_suffix}-{host}"
    temporary_root = "/private/tmp" if sys.platform == "darwin" else "/tmp"
    return f"{temporary_root}/atellagent-{os.getuid()}/{host}.sock"

def _supervisor_agent_path() -> Path:
    if sys.platform == "win32":
        return _service_directory() / "supervisor.task"
    return Path.home() / "Library" / "LaunchAgents" / f"{_SUPERVISOR_LABEL}.plist"

def _supervisor_target() -> str:
    return f"gui/{os.getuid()}/{_SUPERVISOR_LABEL}"

def _supervisor_command() -> list[str]:
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        runner = Path(sys.executable).parent.parent / "atellagent-service" / "atellagent-service.exe"
        if not runner.is_file():
            raise ServiceCommandError("The background service runner is missing; reinstall the desktop package")
        return [str(runner)]
    return [str(_installed_cli_executable()), "service", "run"]

def _supervisor_agent_data(state_directory: Path) -> bytes:
    log_path = state_directory / "supervisor.log"
    return plistlib.dumps({
        "Label": _SUPERVISOR_LABEL,
        "ProgramArguments": _supervisor_command(),
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }, sort_keys=True)

def _supervisor_installed() -> bool:
    if sys.platform == "win32":
        from atellagent_client.cli.windows_service import get_task

        return get_task("supervisor") is not None
    return sys.platform == "darwin" and _read_owned_file(_supervisor_agent_path()) is not None

def _supervisor_loaded() -> bool:
    if sys.platform == "win32":
        from atellagent_client.cli.windows_service import is_running

        return is_running("supervisor", _supervisor_command())
    return _launchctl("print", _supervisor_target(), check=False).returncode == 0

def _supervisor_running() -> bool:
    if sys.platform == "win32":
        return _supervisor_loaded()
    # print reports job registration, even after its process exits. The legacy
    # list contract exposes PID/status/label without parsing diagnostic output.
    result = _launchctl("list", check=False)
    if result.returncode != 0:
        raise ServiceCommandError("Unable to inspect the current-user device service process")
    matches = [fields for line in result.stdout.splitlines()
               if len(fields := line.split()) == 3 and fields[2] == _SUPERVISOR_LABEL]
    if len(matches) != 1:
        return False
    pid, status, _ = matches[0]
    return (pid.isascii() and pid.isdecimal() and 0 < int(pid) <= 2**31 - 1
            and status.isascii() and status.removeprefix("-").isdecimal())

def _validate_supervisor() -> Path:
    path = _supervisor_agent_path()
    if sys.platform == "win32":
        from atellagent_client.cli.windows_service import validate_task

        validate_task("supervisor", _supervisor_command())
        return path
    data = _read_owned_file(path)
    if data is None:
        raise ServiceCommandError("Local controller supervisor is missing")
    try:
        agent = plistlib.loads(data)
    except (TypeError, ValueError) as exc:
        raise ServiceCommandError("Local controller supervisor is invalid") from exc
    if (not isinstance(agent, dict) or agent.get("Label") != _SUPERVISOR_LABEL
            or agent.get("ProgramArguments") != _supervisor_command()):
        raise ServiceCommandError("Local controller supervisor does not match this installation")
    return path

def _launchctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["/bin/launchctl", *arguments],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        raise ServiceCommandError(f"launchctl {arguments[0]} timed out; refresh local service status before retrying") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise ServiceCommandError(f"launchctl {arguments[0]} failed: {detail or result.returncode}")
    return result

def _wait_for_unloaded(target: str, *, timeout_seconds: float = 10.0) -> None:
    """Bootout is asynchronous; do not bootstrap the same label until it is gone."""
    deadline = monotonic() + timeout_seconds
    while _launchctl("print", target, check=False).returncode == 0:
        if monotonic() >= deadline:
            raise ServiceCommandError(f"Local controller service is still stopping: {target}")
        sleep(0.1)

def _installed_cli_executable() -> Path:
    executable = Path(sys.executable).with_name(
        "atellagent-cli.exe" if sys.platform == "win32" else "atellagent-cli"
    )
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ServiceCommandError("Install the atellagent-client command in this Python environment first")
    return executable
