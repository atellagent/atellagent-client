# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Current-user Windows Task Scheduler registration for local controllers."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import subprocess
from time import monotonic, sleep
from typing import Any


class WindowsServiceError(RuntimeError):
    """A safe local task registration failure."""


def current_user_sid() -> str:
    if os.name != "nt":
        raise WindowsServiceError("Windows user service is unavailable on this platform")
    try:
        import win32api
        import win32con
        import win32security

        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
        )
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        return win32security.ConvertSidToStringSid(sid)
    except Exception as exc:
        raise WindowsServiceError("Unable to identify the signed-in Windows user") from exc


def task_name(host: str) -> str:
    suffix = sha256(current_user_sid().encode("utf-8")).hexdigest()[:12]
    return f"AtellagentHookControl-{suffix}-{host}"


def secure_user_path(path: Path) -> None:
    """Apply the same current-user-only ACL used by the local pipe state."""

    from atellagent_client.sdk.windows_security import secure_user_path as protect_path

    try:
        protect_path(path)
    except Exception as exc:
        raise WindowsServiceError("Unable to protect local service state") from exc


def _scheduler() -> tuple[Any, Any]:
    try:
        import win32com.client

        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        return scheduler, scheduler.GetFolder("\\")
    except Exception as exc:
        raise WindowsServiceError("Windows Task Scheduler is unavailable") from exc


def _existing_task(folder: Any, name: str) -> Any | None:
    try:
        return folder.GetTask(name)
    except Exception as exc:
        hresult = getattr(exc, "hresult", None)
        details = getattr(exc, "excepinfo", None)
        if isinstance(details, tuple) and len(details) > 5 and isinstance(details[5], int):
            hresult = details[5]
        if isinstance(hresult, int) and (hresult & 0xFFFFFFFF) == 0x80070002:
            return None
        raise WindowsServiceError("Unable to inspect the local controller task") from exc


def get_task(host: str) -> Any | None:
    _, folder = _scheduler()
    return _existing_task(folder, task_name(host))


def _expected_arguments(command: list[str]) -> str:
    return subprocess.list2cmdline(command[1:])


def _principal_is_current_user(user_id: str) -> bool:
    expected_sid = current_user_sid()
    if user_id == expected_sid:
        return True
    import win32security

    sid, _, _ = win32security.LookupAccountName(None, user_id)
    return win32security.ConvertSidToStringSid(sid) == expected_sid


def validate_task(host: str, command: list[str]) -> Any:
    task = get_task(host)
    if task is None:
        raise WindowsServiceError(f"{host} Windows user task is missing")
    try:
        definition = task.Definition
        action = definition.Actions.Item(1)
        principal = definition.Principal
        if (
            definition.RegistrationInfo.Description
            != f"Atellagent local controller: {host}"
            or str(action.Path).casefold() != str(command[0]).casefold()
            or str(action.Arguments) != _expected_arguments(command)
            or not _principal_is_current_user(str(principal.UserId))
            or int(principal.LogonType) != 3
            or int(principal.RunLevel) != 0
        ):
            raise WindowsServiceError(f"{host} Windows user task does not match this installation")
    except WindowsServiceError:
        raise
    except Exception as exc:
        raise WindowsServiceError("Unable to inspect the local controller task") from exc
    return task


def register_task(host: str, command: list[str]) -> None:
    try:
        scheduler, folder = _scheduler()
        name = task_name(host)
        if _existing_task(folder, name) is not None:
            raise WindowsServiceError(f"{host} Windows user task already exists")
        sid = current_user_sid()
        definition = scheduler.NewTask(0)
        definition.RegistrationInfo.Description = f"Atellagent local controller: {host}"
        definition.Settings.Enabled = True
        definition.Settings.ExecutionTimeLimit = "PT0S"
        definition.Settings.DisallowStartIfOnBatteries = False
        definition.Settings.StopIfGoingOnBatteries = False
        definition.Settings.MultipleInstances = 2  # Ignore a duplicate launch.
        definition.Principal.UserId = sid
        definition.Principal.LogonType = 3  # Existing interactive user token.
        definition.Principal.RunLevel = 0  # Least privilege.
        trigger = definition.Triggers.Create(9)  # Current-user logon.
        trigger.UserId = sid
        action = definition.Actions.Create(0)  # Execute a program.
        action.Path = command[0]
        action.Arguments = _expected_arguments(command)
        folder.RegisterTaskDefinition(name, definition, 2, sid, None, 3)
    except WindowsServiceError:
        raise
    except Exception as exc:
        raise WindowsServiceError("Unable to register the current-user Windows task") from exc


def is_running(host: str, command: list[str]) -> bool:
    task = validate_task(host, command)
    return int(task.State) == 4


def start_task(host: str, command: list[str]) -> None:
    try:
        validate_task(host, command).Run("")
    except WindowsServiceError:
        raise
    except Exception as exc:
        raise WindowsServiceError("Unable to start the current-user Windows task") from exc


def stop_task(host: str, command: list[str]) -> None:
    task = validate_task(host, command)
    if int(task.State) != 4:
        return
    try:
        task.Stop(0)
    except Exception as exc:
        raise WindowsServiceError("Unable to stop the current-user Windows task") from exc


def wait_for_stopped(host: str, command: list[str], *, timeout_seconds: float = 10.0) -> None:
    deadline = monotonic() + timeout_seconds
    while is_running(host, command):
        if monotonic() >= deadline:
            raise WindowsServiceError("Local controller task is still stopping")
        sleep(0.1)


def unregister_task(host: str, command: list[str]) -> None:
    validate_task(host, command)
    _, folder = _scheduler()
    try:
        folder.DeleteTask(task_name(host), 0)
    except Exception as exc:
        raise WindowsServiceError("Unable to remove the current-user Windows task") from exc


__all__ = [
    "WindowsServiceError",
    "current_user_sid",
    "get_task",
    "is_running",
    "register_task",
    "secure_user_path",
    "start_task",
    "stop_task",
    "wait_for_stopped",
    "task_name",
    "unregister_task",
    "validate_task",
]
