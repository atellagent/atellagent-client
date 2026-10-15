"""Shared OS service controls, independent of agent configuration or human login."""

from . import service_platform as service


def install():
    service._require_supported_platform()
    if service._supervisor_installed():
        service._validate_supervisor()
        return
    directory = service._service_directory()
    service._owned_directory(directory, private=True)
    if service.sys.platform == "win32":
        from .windows_service import register_task
        register_task("supervisor", service._supervisor_command())
    else:
        service._write_private_file(service._supervisor_agent_path(),
            service._supervisor_agent_data(directory), private_parent=False)
    print("Installed the current-user device service. It has not been started.")


def start():
    service._require_supported_platform()
    path = service._validate_supervisor()
    loaded = service._supervisor_loaded()
    if loaded and (service.sys.platform == "win32" or service._supervisor_running()):
        print("Device service is already running.")
        return
    if service.sys.platform == "win32":
        from .windows_service import start_task
        start_task("supervisor", service._supervisor_command())
    elif loaded:
        # A registered job with an exited process must be started again. Without
        # -k, a concurrent live owner is never killed or implicitly restarted.
        service._launchctl("kickstart", service._supervisor_target())
    else:
        service._launchctl("bootstrap", f"gui/{service.os.getuid()}", str(path))
    print("Device service started. Controller and reporting readiness are checked separately.")


def stop():
    service._require_supported_platform()
    service._validate_supervisor()
    if service._supervisor_loaded():
        if service.sys.platform == "win32":
            from .windows_service import stop_task, wait_for_stopped
            command = service._supervisor_command()
            stop_task("supervisor", command)
            wait_for_stopped("supervisor", command)
        else:
            service._launchctl("bootout", service._supervisor_target())
            service._wait_for_unloaded(service._supervisor_target())
    print("Device service stopped. Enrollment and approved selections were retained.")


def uninstall():
    service._require_supported_platform()
    path = service._validate_supervisor()
    if service._supervisor_loaded():
        raise service.ServiceCommandError("Stop the device service before uninstalling it")
    if service.sys.platform == "win32":
        from .windows_service import unregister_task
        unregister_task("supervisor", service._supervisor_command())
    else:
        path.unlink()
    print("Removed the OS service registration. Device keys and approved selections were retained.")
