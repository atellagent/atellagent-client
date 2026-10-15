"""Config-free service entrypoint; attended enrollment is a separate setup action."""

import argparse
import json

from . import service_platform as service
from . import native_service_controls as controls
from atellagent_client.onboarding.installation_paths import installation_directory
from .native_service_status import status
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_setup_context import NativeSetupContext
from atellagent_client.onboarding.device_installation import DeviceInstallation


async def run(arguments):
    parser = argparse.ArgumentParser(prog="atellagent-cli service")
    parser.add_argument("action", choices=("install", "start", "stop", "status", "uninstall"))
    parser.add_argument("--json", action="store_true", help="Machine-readable local status")
    args = parser.parse_args(arguments)
    if args.json and args.action != "status":
        parser.error("--json is only valid for status")
    try:
        if args.action == "install":
            async with await NativeSetupContext.open(installation_directory()):
                pass
            controls.install()
        elif args.action == "start":
            # Starting a local process is not admission. The supervisor verifies
            # current device and connection authority before exposing runtimes.
            DeviceInstallation(installation_directory()).load()
            controls.start()
        elif args.action == "status":
            result = await status()
            if args.json:
                print(json.dumps(result, sort_keys=True))
            else:
                state = "running" if result["running"] else "stopped" if result["installed"] else "not installed"
                print(f"Device service: {state}")
                for item in result["controllers"]:
                    print(f"{item['host']}: {'local controller ready' if item['local_controller_ready'] else item['error'] or 'not ready'}")
                print("Local status does not assert server authorization or reporting freshness.")
        else:
            getattr(controls, args.action)()
    except (OnboardingClientError, service.ServiceCommandError, service.WindowsServiceError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, OnboardingClientError) else str(exc)
        parser.exit(1, f"[ERROR] {code}\n")
