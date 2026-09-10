# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""
Lightweight telemetry primitives for SDK integration monitoring.
"""

from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, Mapping, Optional
import httpx

from atellagent_client.protocol.context import apply_workflow_headers, get_workflow_context
from .config import ServiceAccountConfig
from .gateway.session import GatewaySession


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

    gateway_session = GatewaySession.from_service_account_config(config)

    def _emit(event: TelemetryEvent) -> None:
        try:
            payload, workflow_context = _event_payload_and_workflow_context(event)
            headers = apply_workflow_headers(
                {},
                workflow_context=workflow_context or get_workflow_context(),
            )
            gateway_session.request_authenticated_sync(
                "POST", telemetry_url, json=payload, headers=headers
            )
        except Exception:
            return

    return _emit
