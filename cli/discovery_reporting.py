# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Device-owned local preferences; explicit online label update."""

import argparse
from datetime import datetime, timezone
import json
from uuid import UUID

from atellagent_client.onboarding.device_installation import DeviceInstallation
from atellagent_client.onboarding.device_label_cache import DeviceLabelCache
from atellagent_client.onboarding.device_runtime import DeviceRuntime
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.reporting_scope import ReportingScope
from atellagent_client.onboarding.reporting_state import DeviceReportingStore
from atellagent_client.onboarding.installation_paths import installation_directory


def _local_installation():
    installation = DeviceInstallation(installation_directory())
    try:
        installation.path.lstat()
    except FileNotFoundError:
        return None
    draft, receipt, _ = installation.load()
    scope = ReportingScope(draft.tenant_id, receipt.device_id, draft.owner_user_id)
    return installation, draft, DeviceReportingStore(installation.directory / "reporting", scope)


def _status(draft, store):
    try:
        state = store.load()
    except FileNotFoundError:
        label, uploaded = "not_started", None
    else:
        label = ("enable pending" if state.enabled else "removal pending (off)") if state.pending_sync else (
            "enabled" if state.enabled else "disabled")
        uploaded = None if not state.uploaded_at else datetime.fromtimestamp(
            state.uploaded_at, timezone.utc).isoformat().replace("+00:00", "Z")
    name = DeviceLabelCache(store.directory.parent, store.scope).load()
    return {"device_id": str(store.scope.device_id), "tenant_id": str(store.scope.tenant_id),
        "device_name": draft.display_name if name is None else name, "state": label,
        "last_successful_upload_at": uploaded, "state_source": "local_preference"}


async def run(arguments):
    parser = argparse.ArgumentParser(prog="atellagent-cli discover reporting")
    parser.add_argument("action", choices=("list", "status", "enable", "disable", "rename"))
    parser.add_argument("--name", help="Editable display label, not an identity")
    parser.add_argument("--device-id", help="Confirm the device in this installation")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    if (args.action == "rename") != (args.name is not None):
        parser.error("rename requires --name; --name is valid only for rename")
    if args.json and args.action not in {"list", "status"}:
        parser.error("--json is valid only for list/status")
    try:
        selected = _local_installation()
        if args.action == "list" and selected is None:
            print(json.dumps({"installations": []}) if args.json else "No enrolled device installation.")
            return
        if selected is None:
            raise OnboardingClientError("device_installation_unavailable")
        installation, draft, store = selected
        if args.device_id and UUID(args.device_id) != store.scope.device_id:
            raise OnboardingClientError("device_identity_mismatch")
        if args.action in {"list", "status"}:
            value = _status(draft, store)
            print(json.dumps({"installations": [value]} if args.action == "list" else value) if args.json else
                f"{value['device_name']}: discovery reporting {value['state']} (local preference).")
        elif args.action == "rename":
            DeviceLabelCache.validate_name(args.name)
            runtime = await DeviceRuntime.open(installation.directory)
            try:
                await runtime.client.rename(args.name)
                DeviceLabelCache(installation.directory, store.scope).save(args.name)
            finally:
                await runtime.client.aclose()
            print("Device name updated.")
        else:
            if not store.set_enabled(args.action == "enable"):
                raise OnboardingClientError("device_reporting_busy", retryable=True)
            print("Reporting preference saved. " + ("Uploads will resume on the next service cycle." if args.action == "enable"
                else "Reporting is off; snapshot deletion will retry on the next service cycle. Agent authorization is unchanged."))
    except (OnboardingClientError, OSError, ValueError, OverflowError) as exc:
        code = exc.code if isinstance(exc, OnboardingClientError) else "device_reporting_state_unavailable"
        parser.exit(1, f"[ERROR] {code}\n")
