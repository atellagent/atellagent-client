# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Initialization helpers for Atellagent SDK client construction."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from atellagent_client.sdk.auth import AuthManager
from atellagent_client.protocol.api import strip_api_suffix
from atellagent_client.sdk.config import ServiceAccountConfig
from atellagent_client.sdk.http import HTTPClientManager
from atellagent_client.sdk.tls import build_gateway_cert_validator, build_oauth_cert_validator


def resolve_client_transport_settings(
    *,
    service_account_config: Optional[ServiceAccountConfig],
    timeout: float,
) -> Tuple[
    Optional[Tuple[str, str]],
    float,
]:
    cert_tuple = None
    effective_timeout = timeout

    if service_account_config:
        if service_account_config.cert_path and service_account_config.key_path:
            cert_tuple = (
                service_account_config.cert_path,
                service_account_config.key_path,
            )
        effective_timeout = service_account_config.timeout or timeout

    return cert_tuple, effective_timeout


def resolve_base_url(
    *,
    service_account_config: ServiceAccountConfig,
) -> str:
    return strip_api_suffix(service_account_config.gateway_url)


def build_http_client_manager(
    *,
    auth_manager: AuthManager,
    base_url: str,
    service_account_config: Optional[ServiceAccountConfig],
    timeout: float,
) -> tuple[HTTPClientManager, Optional[HTTPClientManager]]:
    cert_tuple, effective_timeout = (
        resolve_client_transport_settings(
            service_account_config=service_account_config,
            timeout=timeout,
        )
    )
    http_client_manager = HTTPClientManager(
        effective_timeout,
        cert=cert_tuple,
        server_identity_validator=build_gateway_cert_validator(base_url)
        if service_account_config
        else None,
    )

    oauth_http_client_manager: Optional[HTTPClientManager] = None
    if service_account_config:
        token_url = auth_manager.get_token_url()
        oauth_http_client_manager = HTTPClientManager(
            effective_timeout,
            cert=cert_tuple,
            server_identity_validator=build_oauth_cert_validator(token_url),
        )
    return http_client_manager, oauth_http_client_manager


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
    "build_http_client_manager",
    "build_telemetry_context",
    "resolve_base_url",
]
