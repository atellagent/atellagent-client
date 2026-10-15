# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Confirm retained replacements using current authority, never stale parent TLS."""

import json
import asyncio
from itertools import islice
from cryptography.exceptions import UnsupportedAlgorithm

from .atomic_private_file import read_private_file
from .device import DeviceManagementClient
from .device_renewal_loading import read_device_renewal_generation
from .device_renewal_preparation import load_retained_device_renewal_preparation, private_key_pem
from .device_renewal_stage import StagedDeviceRenewal, select_device_renewal
from .errors import OnboardingClientError
from .private_paths import require_private_path, require_safe_ancestry


def retained_device_candidate(*, device, trust):
    try:
        root = device.certificate_path.parent
        require_safe_ancestry(root)
        require_private_path(root, directory=True)
        pointers = list(islice(root.glob("device-renewal-????????-????-????-????-????????????.json"), 2))
        if len(pointers) > 1:
            raise ValueError("one retained proposal per device generation required")
        if not pointers:
            return None
        prepared = load_retained_device_renewal_preparation(device=device, trust=trust)
        pointer = root / f"device-renewal-{prepared.operation_id}.json"
        raw = read_private_file(pointer, limit=4096)
        record = json.loads(raw)
        candidate, verified = read_device_renewal_generation(device=device, trust=trust,
            record=record, history_only=False)
        if (record["operation_id"] != str(prepared.operation_id)
                or record["parent_certificate_sha256"] != prepared.parent_certificate_sha256
                or record["public_key_sha256"] != prepared.public_key_sha256
                or read_private_file(candidate.private_key_path, limit=8192) != private_key_pem(prepared.private_key).encode()):
            raise ValueError("retained candidate differs from exact prepared replacement")
        staged = StagedDeviceRenewal(prepared, verified, pointer,
            candidate.certificate_path, candidate.private_key_path, raw)
        return candidate, staged
    except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError, UnsupportedAlgorithm) as exc:
        raise OnboardingClientError("device_renewal_recovery_unavailable") from exc


async def reconcile_device_renewal_candidate(*, device, trust, server_ca_path=None):
    retained = await asyncio.to_thread(retained_device_candidate, device=device, trust=trust)
    if retained is None:
        return device
    candidate, staged = retained
    async with DeviceManagementClient(origin=trust.device_api_origin, device=candidate,
        server_ca_path=server_ca_path) as replacement:
        try:
            receipt = await replacement._request("GET",
                f"/devices/self/certificate-renewals/{staged.prepared.operation_id}/activation")
        except OnboardingClientError as exc:
            if exc.status in {403, 404}:
                # This is not evidence of activation. Caller must still verify
                # current parent material and authority before continuing.
                return device
            raise
        await asyncio.to_thread(select_device_renewal, staged=staged, receipt=receipt)
    return candidate
