# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Exact renewal over separate parent/replacement TLS pools, without human login."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from .contract_values import aware_time
from .device import DeviceManagementClient
from .device_renewal_preparation import prepare_device_renewal
from .device_renewal_stage import stage_device_renewal, select_device_renewal
from .errors import OnboardingClientError
from .device_renewal_loading import load_device_renewal_history, load_selected_device_renewals
from .device_renewal_recovery import reconcile_device_renewal_candidate

PREFIX = "/devices/self/certificate-renewals"


def require_renewal_progress(payload, *, prepared, device, deadline=None):
    try:
        expected = {"operation_id", "status", "device_id", "public_key_sha256", "deadline_at"}
        if payload.get("status") == "issued" and "certificate_pem" in payload:
            expected |= {"certificate_pem", "certificate_chain_pem", "certificate_sha256", "expires_at"}
        if (set(payload) != expected or payload["operation_id"] != str(prepared.operation_id)
                or payload["device_id"] != str(device.device_id)
                or payload["public_key_sha256"] != prepared.public_key_sha256):
            raise ValueError("exact renewal progress required")
        current = aware_time(payload["deadline_at"])
        if deadline is not None and current != deadline:
            raise ValueError("renewal deadline changed")
        if payload["status"] not in {"pending", "in_progress", "issued", "failed", "cancelled"}:
            raise ValueError("closed renewal status required")
        if payload["status"] in {"failed", "cancelled"} or current <= datetime.now(timezone.utc):
            raise OnboardingClientError("device_renewal_recovery_required")
        return current
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise OnboardingClientError("device_renewal_response_invalid") from exc


async def renew_device_certificate(client, *, trust):
    if client.origin != trust.device_api_origin:
        raise OnboardingClientError("device_renewal_origin_mismatch")
    device = client.device
    retained = await asyncio.to_thread(load_device_renewal_history, device=device, trust=trust)
    recovered = await reconcile_device_renewal_candidate(device=retained, trust=trust,
        server_ca_path=client._server_ca_path)
    current = await asyncio.to_thread(load_selected_device_renewals, device=recovered, trust=trust)
    if current.certificate_path != device.certificate_path:
        async with DeviceManagementClient(origin=client.origin, device=current,
            server_ca_path=client._server_ca_path) as replacement:
            await replacement.status()
        await client.replace_credentials(current)
        return current
    prepared = await asyncio.to_thread(prepare_device_renewal, device=device, trust=trust)
    response = await client._request("POST", PREFIX,
        {"operation_id": str(prepared.operation_id), "csr_pem": prepared.csr_pem})
    deadline = require_renewal_progress(response, prepared=prepared, device=device)
    try:
        async with asyncio.timeout(min(900, (deadline - datetime.now(timezone.utc)).total_seconds())):
            while True:
                issued = await client._request("GET", f"{PREFIX}/{prepared.operation_id}")
                require_renewal_progress(issued, prepared=prepared, device=device, deadline=deadline)
                if issued["status"] == "issued":
                    staged = await asyncio.to_thread(stage_device_renewal, device=device,
                        trust=trust, prepared=prepared, payload=issued)
                    break
                await asyncio.sleep(2)
    except TimeoutError as exc:
        raise OnboardingClientError("device_renewal_wait_timed_out", retryable=True) from exc
    replacement_device = replace(device, certificate_path=staged.certificate_path, private_key_path=staged.private_key_path)
    replacement = DeviceManagementClient(origin=client.origin, device=replacement_device,
        server_ca_path=client._server_ca_path)
    try:
        try:
            receipt = await replacement._request("POST", f"{PREFIX}/{prepared.operation_id}/activate",
                {"certificate_sha256": staged.credential.certificate_sha256})
            await asyncio.to_thread(select_device_renewal, staged=staged, receipt=receipt)
        except OnboardingClientError:
            # This read is safe even when the activation reply was lost. A denied
            # receipt never selects material or retries activation with guessed authority.
            receipt = await replacement._request("GET", f"{PREFIX}/{prepared.operation_id}/activation")
            await asyncio.to_thread(select_device_renewal, staged=staged, receipt=receipt)
        await client.replace_credentials(replacement_device)
        return replacement_device
    finally:
        await replacement.aclose()
