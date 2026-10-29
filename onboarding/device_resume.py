# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Resume committed devices only after a fresh signed mTLS authority/status check."""

import asyncio

from .device import DeviceManagementClient
from .device_activation import ActivatedDevice
from .device_activation_receipt import require_activation_receipt
from .device_identity import AdmittedDevice
from .device_renewal_loading import load_selected_device_renewals, load_device_renewal_history
from .device_renewal_recovery import reconcile_device_renewal_candidate
from .errors import OnboardingClientError


def _retained_candidate(*, draft, receipt, trust, setup_store, credential_store, fingerprint):
    saved = setup_store.require_saved(draft, expected_origin=trust.platform_origin)
    stored = credential_store.load_history(draft=saved, receipt=receipt, trust=trust, fingerprint=fingerprint)
    require_activation_receipt(saved, stored)
    candidate = AdmittedDevice(saved.tenant_id, stored.credential.device_id,
        saved.owner_user_id, saved.keys.proof_key, stored.certificate_path, stored.private_key_path)
    return load_device_renewal_history(device=candidate, trust=trust)


async def resume_stored_device(*, draft, receipt, trust, setup_store, credential_store,
    fingerprint: str) -> ActivatedDevice:
    candidate = await asyncio.to_thread(_retained_candidate, draft=draft, receipt=receipt, trust=trust,
        setup_store=setup_store, credential_store=credential_store, fingerprint=fingerprint)
    candidate = await reconcile_device_renewal_candidate(device=candidate, trust=trust)
    candidate = await asyncio.to_thread(load_selected_device_renewals, device=candidate, trust=trust)
    async with DeviceManagementClient(origin=trust.device_api_origin, device=candidate) as client:
        status = await client.status()
    if status.discovery_reporting_status not in {"enabled", "disabled"}:
        raise OnboardingClientError("device_reporting_state_unavailable")
    return ActivatedDevice(candidate, status.discovery_reporting_status == "enabled")
