# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Explicit recoverable local removal; never revokes or transfers cloud authority."""

from uuid import uuid4

from .atomic_private_file import sync_directory
from .device_installation import DeviceInstallation
from .errors import OnboardingClientError
from .private_paths import create_private_directory, require_private_path, require_safe_ancestry
from .process_lease import process_lease


def archive_device_installation(directory, *, expected_device_id, service_running):
    if directory.name != "installation" or service_running:
        raise OnboardingClientError("device_archive_stop_service_required")
    require_safe_ancestry(directory)
    require_private_path(directory, directory=True)
    require_private_path(directory.parent, directory=True)
    # Locks live outside the directory being moved, including on Windows where
    # an open file in the source directory could prevent the move.
    with process_lease(directory.parent / ".device-setup.lock") as setup_held:
        if not setup_held:
            raise OnboardingClientError("onboarding_setup_busy", retryable=True)
        with process_lease(directory.parent / ".device-runtime.lock") as runtime_held:
            if not runtime_held:
                raise OnboardingClientError("device_archive_stop_service_required")
            installation = DeviceInstallation(directory)
            _, _, receipt, _ = installation.metadata()
            if receipt.device_id != expected_device_id:
                raise OnboardingClientError("device_archive_identity_changed")
            return retain_installation_archive(directory)


def retain_installation_archive(directory):
    """Caller holds setup/runtime leases and has verified the exact selection."""
    if directory.name != "installation":
        raise OnboardingClientError("device_archive_identity_changed")
    require_safe_ancestry(directory)
    require_private_path(directory, directory=True)
    require_private_path(directory.parent, directory=True)
    archive = directory.parent / f"installation-archived-{uuid4().hex}"
    create_private_directory(archive, exclusive=True)
    directory.rename(archive / "installation")
    try:
        sync_directory(archive)
        sync_directory(directory.parent)
    except OSError as exc:
        raise OnboardingClientError("device_archive_outcome_uncertain") from exc
    return archive
