# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Proof-only recovery after a lost submission response; no new login or keys."""

from .device_setup_flow import DeviceSetupFlow
from .setup_receipt import SetupReceipt
from .setup_status import SetupStatusClient
from .errors import OnboardingClientError
from .pending_device_setup import submission_is_unconfirmed


async def submit_pending_device_setup(management, pending):
    draft, _, _ = pending.load()
    receipt = await management.submit(draft=draft, store=pending.installation.setups)
    pending.bind_receipt(receipt)
    return receipt


async def resume_pending_device_setup(pending) -> DeviceSetupFlow:
    draft, receipt, enabled = pending.load()
    if receipt is None:
        async with SetupStatusClient(origin=draft.platform_origin, operation_id=draft.operation_id,
            proof_key=draft.keys.proof_key) as client:
            try:
                progress = await client.status()
            except OnboardingClientError as error:
                if submission_is_unconfirmed(receipt, error):
                    raise OnboardingClientError("onboarding_setup_submission_unconfirmed") from error
                raise
        # This is the single current revision, not an old-format conversion.
        receipt = SetupReceipt.from_payload({"id": str(progress.id), "kind": progress.kind,
            "status": progress.status, "device_id": None if progress.device_id is None else str(progress.device_id),
            "revision": 1, "expires_at": progress.expires_at.isoformat(),
            "claim_expires_at": None if progress.claim_expires_at is None else progress.claim_expires_at.isoformat()},
            operation_id=draft.operation_id, kind="device")
        pending.bind_receipt(receipt)
    return DeviceSetupFlow(installation=pending.installation, draft=draft, receipt=receipt, reporting_enabled=enabled)
