# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""
Lightweight telemetry primitives for SDK integration monitoring.
"""

from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, Mapping, Optional
import httpx

from .auth import AuthManager
from atellagent_client.protocol.context import apply_workflow_headers, get_workflow_context
from .config import ServiceAccountConfig
from .http import HTTPClientManager
from .tls import (
    build_gateway_cert_validator,
    build_oauth_cert_validator,
)


@dataclass
class TelemetryEvent:
    integration_type: str  # 'agent' | 'mcp'
    service_account_id: Optional[str] = (
        None  # preferred; immutable identifier supplied in the provisioned bundle
    )
    auth_client_id: Optional[str] = None  # machine auth binding
    agent_deployment_id: Optional[str] = None
    mcp_server_id: Optional[str] = None
    method: Optional[str] = None
    endpoint: Optional[str] = None
    status_code: Optional[int] = None
    response_time_ms: Optional[int] = None
    tokens_used: Optional[int] = None
    policy_result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    request_id: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


TelemetryEmitter = Callable[[TelemetryEvent], None]


def _event_payload_and_workflow_context(
    event: TelemetryEvent,
) -> tuple[Dict[str, Any], Optional[Mapping[str, Any]]]:
    payload = asdict(event)
    extra = payload.get("extra")
    workflow_context = None
    if isinstance(extra, dict):
        candidate = extra.pop("workflow_context", None)
        if isinstance(candidate, Mapping):
            workflow_context = candidate
        if not extra:
            payload["extra"] = None
    return payload, workflow_context


def make_http_emitter(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 0.5,
) -> TelemetryEmitter:
    """
    Build a simple HTTP emitter that posts TelemetryEvent JSON to the given URL.
    Best-effort: errors are swallowed, short timeout to avoid impacting callers.
    """

    def _emit(event: TelemetryEvent) -> None:
        try:
            payload, _workflow_context = _event_payload_and_workflow_context(event)
            httpx.post(url, json=payload, headers=headers, timeout=timeout)
        except Exception:
            # swallow errors to keep caller fast
            return

    return _emit


def make_authenticated_telemetry_emitter(
    config: ServiceAccountConfig,
    *,
    telemetry_url_override: Optional[str] = None,
) -> TelemetryEmitter:
    """
    Build a telemetry emitter that reuses service-account auth + mTLS settings.
    Best-effort: exceptions are swallowed.
    """
    telemetry_url = telemetry_url_override or getattr(config, "telemetry_url", None)
    if not telemetry_url:
        return lambda event: None

    auth_manager = AuthManager(service_account_config=config)
    cert_tuple = None
    if config.cert_path and config.key_path:
        cert_tuple = (config.cert_path, config.key_path)

    telemetry_validator = None
    if config.gateway_url and telemetry_url.startswith(config.gateway_url):
        telemetry_validator = build_gateway_cert_validator(config.gateway_url)

    oauth_validator = build_oauth_cert_validator(auth_manager.get_token_url())

    http_manager = HTTPClientManager(
        timeout=config.timeout,
        cert=cert_tuple,
        server_identity_validator=telemetry_validator,
    )
    oauth_manager = HTTPClientManager(
        timeout=config.timeout,
        cert=cert_tuple,
        server_identity_validator=oauth_validator,
    )

    def _emit(event: TelemetryEvent) -> None:
        try:
            client = http_manager.get_sync_client()
            auth_client = oauth_manager.get_sync_client()
            if not auth_manager.ensure_authenticated_sync(auth_client):
                return
            payload, workflow_context = _event_payload_and_workflow_context(event)
            headers = apply_workflow_headers(
                auth_manager.get_auth_headers(),
                workflow_context=workflow_context or get_workflow_context(),
            )
            client.post(telemetry_url, json=payload, headers=headers)
        except Exception:
            return

    return _emit
