"""Confirmed local archive; fresh setup still follows normal admin approval."""

import argparse
import json

from atellagent_client.onboarding.installation_paths import installation_directory
from . import service_platform as service
from atellagent_client.onboarding.contract_values import canonical_id
from atellagent_client.onboarding.device_archive import archive_device_installation
from atellagent_client.onboarding.errors import OnboardingClientError


async def run(arguments):
    parser = argparse.ArgumentParser(prog="atellagent-cli setup archive",
        description="Archive this stopped device's local state. Cloud authority and host hooks are not changed.")
    parser.add_argument("--confirm-device", required=True, help="Exact device ID being removed locally")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        device_id = canonical_id(args.confirm_device)
        running = service._supervisor_loaded() if service.sys.platform in {"darwin", "win32"} else False
        archive = archive_device_installation(installation_directory(),
            expected_device_id=device_id, service_running=running)
        summary = ("Local state archived; private keys were retained in the archive. Cloud permissions and host hooks "
            "are unchanged. Revoke the old device in Discovery; fresh setup and agent connections require approval.")
        if args.json:
            print(json.dumps(dict(status="archived", device_id=str(device_id), archive_path=str(archive), summary=summary)))
        else:
            print(f"{summary}\nArchive: {archive}")
    except (OnboardingClientError, OSError, ValueError, service.ServiceCommandError) as exc:
        code = exc.code if isinstance(exc, OnboardingClientError) else "device_archive_unavailable"
        parser.exit(1, f"[ERROR] {code}; refresh setup and inspect local archives before retrying.\n")
