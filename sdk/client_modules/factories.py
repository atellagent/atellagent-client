# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Convenience constructors for the Atellagent SDK client."""

from __future__ import annotations

import math
from typing import Optional

from atellagent_client.onboarding.workload_runtime import open_workload_runtime
from atellagent_client.onboarding.workload_setup_flow import WorkloadSetupFlow
from atellagent_client.sdk.telemetry import TelemetryEmitter
from .client_class import AtellagentClient


async def create_service_account_client(
    *,
    flow: WorkloadSetupFlow,
    deployment: Optional[dict] = None,
    local_guardrail_manifest_path: Optional[str] = None,
    timeout: float = 120.0,
    telemetry_emitter: Optional[TelemetryEmitter] = None,
) -> AtellagentClient:
    """Resume approved setup and open a machine-authenticated SDK client.

    No browser login is initiated here. Pending or denied setup raises a bounded
    onboarding error; it never creates a replacement identity or bypasses approval.
    """
    if not isinstance(flow, WorkloadSetupFlow):
        raise TypeError("approved workload setup flow required")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("positive finite SDK timeout required")
    config = await open_workload_runtime(flow=flow, deployment=deployment,
                                       local_guardrail_manifest_path=local_guardrail_manifest_path)
    config.timeout = float(timeout)
    return AtellagentClient(
        service_account_config=config,
        timeout=timeout,
        telemetry_emitter=telemetry_emitter,
        integration_type=config.integration_type,
        service_account_id=config.service_account_id,
        device_proof_provider=flow.device_request_provider(),
    )

__all__ = ["create_service_account_client"]
