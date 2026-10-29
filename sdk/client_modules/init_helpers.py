# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Initialization helpers for Atellagent SDK client construction."""

from __future__ import annotations

from typing import Any, Dict, Optional

from atellagent_client.sdk.config import ServiceAccountConfig


def build_telemetry_context(
    *,
    service_account_config: ServiceAccountConfig,
    integration_type: Optional[str],
    service_account_id: Optional[str],
) -> Dict[str, Any]:
    derived_integration_type = integration_type or (
        service_account_config.integration_type or "agent"
    )
    sa_id = service_account_id
    if not sa_id:
        sa_id = service_account_config.service_account_id
    sa_client_id = service_account_config.auth_client_id
    return {
        "integration_type": derived_integration_type,
        "service_account_id": sa_id,
        "auth_client_id": sa_client_id,
        "agent_deployment_id": None,
        "mcp_server_id": None,
    }


__all__ = [
    "build_telemetry_context",
]
