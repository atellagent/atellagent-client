# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Durable current device request; resuming never generates replacement keys."""

import json
from uuid import UUID

from .atomic_private_file import create_private_file, read_private_file
from .errors import OnboardingClientError
from .private_record_update import replace_private_record
from .receipt_encoding import receipt_payload
from .process_lease import process_lease
from .setup_receipt import SetupReceipt


class PendingDeviceSetupStore:
    def __init__(self, installation):
        self.installation = installation
        self.path = installation.directory / "pending-device.json"
        self.lease_path = installation.directory / ".pending-device.lock"

    def begin(self, draft, *, reporting_enabled: bool):
        if draft.kind != "device" or type(reporting_enabled) is not bool:
            raise ValueError("exact device request and reporting choice required")
        saved = self.installation.setups.require_saved(draft, expected_origin=draft.platform_origin)
        payload = {"revision": 1, "setup_id": str(saved.operation_id), "tenant_id": str(saved.tenant_id),
            "owner_user_id": str(saved.owner_user_id), "platform_origin": saved.platform_origin,
            "reporting_enabled": reporting_enabled, "receipt": None}
        with process_lease(self.lease_path) as held:
            if not held:
                raise OnboardingClientError("onboarding_setup_busy", retryable=True)
            try:
                self.installation.path.lstat()
            except FileNotFoundError:
                pass
            else:
                raise OnboardingClientError("device_installation_already_selected")
            try:
                create_private_file(self.path, json.dumps(payload, sort_keys=True).encode())
            except FileExistsError:
                current, _, enabled = self.load()
                if current.encode() != saved.encode() or enabled is not reporting_enabled:
                    raise OnboardingClientError("onboarding_pending_setup_conflict")

    def load(self):
        try:
            value = json.loads(read_private_file(self.path, limit=4096))
            if (not isinstance(value, dict) or set(value) != {"revision", "setup_id", "tenant_id",
                "owner_user_id", "platform_origin", "reporting_enabled", "receipt"}
                or type(value["revision"]) is not int or value["revision"] != 1
                or type(value["reporting_enabled"]) is not bool):
                raise ValueError("closed pending device setup required")
            identifiers = {key: UUID(value[key]) for key in ("setup_id", "tenant_id", "owner_user_id")}
            if any(str(item) != value[key] for key, item in identifiers.items()):
                raise ValueError("canonical pending setup required")
            draft = self.installation.setups.load(identifiers["setup_id"], expected_origin=value["platform_origin"],
                expected_tenant_id=identifiers["tenant_id"], expected_owner_user_id=identifiers["owner_user_id"])
            if draft.kind != "device":
                raise ValueError("pending device setup required")
            receipt = None if value["receipt"] is None else SetupReceipt.from_payload(value["receipt"],
                operation_id=draft.operation_id, kind="device")
            return draft, receipt, value["reporting_enabled"]
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_pending_setup_unavailable") from exc

    def bind_receipt(self, receipt):
        with process_lease(self.lease_path) as held:
            if not held:
                raise OnboardingClientError("onboarding_setup_busy", retryable=True)
            draft, previous, _ = self.load()
            verified = SetupReceipt.from_payload(receipt_payload(receipt),
                operation_id=draft.operation_id, kind="device")
            if previous is not None and previous.device_id != verified.device_id:
                raise OnboardingClientError("onboarding_setup_identity_changed")
            if previous is not None:
                return  # Mutable progress is fetched with the same retained key.
            payload = json.loads(read_private_file(self.path, limit=4096))
            payload["receipt"] = receipt_payload(verified)
            replace_private_record(self.path, json.dumps(payload, sort_keys=True).encode(), limit=4096)


def submission_is_unconfirmed(receipt, error):
    """A missing receipt plus opaque proof denial is uncertainty, never absence or approval."""
    return (receipt is None and error.status == 403
            and error.code == "onboarding_permission_denied")
