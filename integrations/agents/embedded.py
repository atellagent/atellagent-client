# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public embedded-SDK entrypoints for callable-agent integrations."""

from __future__ import annotations

from typing import Optional

from atellagent_client.sdk.client import AtellagentClient
from atellagent_client.sdk.config import SDKDeploymentConfig, ServiceAccountConfig
from atellagent_client.sdk.telemetry import (
    TelemetryEmitter,
    make_authenticated_telemetry_emitter,
)

from .identity_mode import (
    BOUNDARY_IDENTITY_ONLY,
    ExternalAgentIdentityMode,
    normalize_identity_mode,
)


def create_embedded_sdk_client(
    *,
    client_id: str,
    gateway_url: str,
    oauth_token_url: Optional[str] = None,
    oauth_jwks_url: Optional[str] = None,
    timeout: float = 120.0,
    cert_path: Optional[str] = None,
    key_path: Optional[str] = None,
    ca_path: Optional[str] = None,
    telemetry_emitter: Optional[TelemetryEmitter] = None,
    integration_type: str = "agent",
    service_account_id: Optional[str] = None,
    telemetry_url: Optional[str] = None,
    api_version: str = "v1",
    contract_version: str = "v1",
    identity_mode: ExternalAgentIdentityMode = BOUNDARY_IDENTITY_ONLY,
) -> AtellagentClient:
    """
    Create the supported embedded SDK surface for direct agent-process integration.

    `identity_mode` is explicit at the public wedge layer even when the runtime
    remains boundary-only.
    """

    config = ServiceAccountConfig(
        client_id=client_id,
        gateway_url=gateway_url,
        oauth_token_url=oauth_token_url,
        oauth_jwks_url=oauth_jwks_url,
        service_account_id=service_account_id,
        telemetry_url=telemetry_url,
        api_version=api_version,
        contract_version=contract_version,
        cert_path=cert_path,
        key_path=key_path,
        ca_path=ca_path,
        timeout=timeout,
        integration_type=integration_type,
        deployment=SDKDeploymentConfig(
            identity_mode=normalize_identity_mode(identity_mode),
        ),
    )

    effective_emitter = telemetry_emitter
    if (
        not effective_emitter
        and config.telemetry_url
        and str(config.integration_type or "").lower() != "agent"
    ):
        effective_emitter = make_authenticated_telemetry_emitter(config)

    return AtellagentClient(
        service_account_config=config,
        timeout=timeout,
        telemetry_emitter=effective_emitter,
        integration_type=integration_type,
        service_account_id=service_account_id or config.service_account_id,
    )


__all__ = ["create_embedded_sdk_client"]
