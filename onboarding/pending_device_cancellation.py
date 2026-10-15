# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Confirmed unfinished-device cancellation and recoverable local archival."""

from .device_archive import retain_installation_archive
from .device_installation import DeviceInstallation
from .errors import OnboardingClientError
from .pending_device_setup import PendingDeviceSetupStore
from .private_paths import require_private_path, require_safe_ancestry
from .process_lease import process_lease
from .setup_status import SetupStatusClient


async def cancel_pending_device(directory, *, expected_setup_id):
    if directory.name != "installation":
        raise OnboardingClientError("device_archive_identity_changed")
    require_safe_ancestry(directory)
    require_private_path(directory, directory=True)
    require_private_path(directory.parent, directory=True)
    with process_lease(directory.parent / ".device-setup.lock") as setup_held:
        if not setup_held:
            raise OnboardingClientError("onboarding_setup_busy", retryable=True)
        with process_lease(directory.parent / ".device-runtime.lock") as runtime_held:
            if not runtime_held:
                raise OnboardingClientError("device_archive_stop_service_required")
            installation = DeviceInstallation(directory)
            try:
                installation.path.lstat()
            except FileNotFoundError:
                pass
            else:
                raise OnboardingClientError("device_installation_already_selected")
            pending = PendingDeviceSetupStore(installation)
            draft, receipt, _ = pending.load()
            if draft.operation_id != expected_setup_id:
                raise OnboardingClientError("device_archive_identity_changed")
            async with SetupStatusClient(origin=draft.platform_origin,
                operation_id=draft.operation_id, proof_key=draft.keys.proof_key) as client:
                current = await client.status()
                if current.kind != "device" or (receipt is not None and current.device_id != receipt.device_id):
                    raise OnboardingClientError("onboarding_setup_identity_changed")
                if current.status in {"pending", "approved"}:
                    current = await client.cancel()
                if current.kind != "device" or current.status not in {"cancelled", "denied", "expired", "failed"}:
                    raise OnboardingClientError("onboarding_conflict")
            # A lost response or activation race leaves all selection/material
            # intact. Never archive consumed admission as an unfinished request.
            return retain_installation_archive(directory)
