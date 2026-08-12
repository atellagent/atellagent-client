# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""SDK channel ingress operation handlers."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import httpx

from atellagent_client.protocol.api import build_versioned_route
from atellagent_client.sdk.errors import PolicyViolationError
from atellagent_client.sdk.telemetry import TelemetryEmitter, TelemetryEvent
from .common import extract_policy_detail

logger = logging.getLogger(__name__)


def _build_channel_ingress_payload(
    *,
    event: Dict[str, Any],
    target: Optional[Dict[str, Any]],
    input_data: Optional[Dict[str, Any]],
    execution_config: Optional[Dict[str, Any]],
    channel_type: Optional[str],
    provider_key: Optional[str],
    adapter_key: Optional[str],
    idempotency_key: Optional[str],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "event": event if isinstance(event, dict) else {},
        "target": target if isinstance(target, dict) else {},
        "input_data": input_data if isinstance(input_data, dict) else {},
        "execution_config": execution_config if isinstance(execution_config, dict) else {},
    }
    if channel_type:
        payload["channel_type"] = str(channel_type)
    if provider_key:
        payload["provider_key"] = str(provider_key)
    if adapter_key:
        payload["adapter_key"] = str(adapter_key)
    if idempotency_key:
        payload["idempotency_key"] = str(idempotency_key)
    return payload


def _emit_channel_ingress_telemetry(
    *,
    endpoint: str,
    start: float,
    status_code: int,
    telemetry_emitter: Optional[TelemetryEmitter],
    telemetry_context: Optional[Dict[str, Any]],
    policy_result: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
) -> None:
    if not telemetry_emitter:
        return
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    try:
        telemetry_emitter(
            TelemetryEvent(
                **(telemetry_context or {}),
                method="POST",
                endpoint=endpoint,
                status_code=status_code,
                response_time_ms=elapsed_ms,
                policy_result=policy_result,
                error_message=error_message,
            )
        )
    except Exception:
        logger.debug("telemetry emission failed", exc_info=True)


def _handle_channel_ingress_response(
    *,
    endpoint: str,
    response: httpx.Response,
    start: float,
    telemetry_emitter: Optional[TelemetryEmitter],
    telemetry_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if response.status_code in (200, 201, 202):
        data = response.json() if response.content else {}
        _emit_channel_ingress_telemetry(
            endpoint=endpoint,
            start=start,
            status_code=response.status_code,
            telemetry_emitter=telemetry_emitter,
            telemetry_context=telemetry_context,
        )
        return data if isinstance(data, dict) else {}

    if response.status_code == 403:
        error_data: Dict[str, Any]
        try:
            error_data = response.json()
        except Exception:
            error_data = {}
        detail = extract_policy_detail(error_data)
        _emit_channel_ingress_telemetry(
            endpoint=endpoint,
            start=start,
            status_code=response.status_code,
            telemetry_emitter=telemetry_emitter,
            telemetry_context=telemetry_context,
            policy_result=detail or None,
            error_message=detail.get("message") if isinstance(detail, dict) else None,
        )
        raise PolicyViolationError(
            "Channel ingress blocked by policy",
            detail.get("violation_type", "unknown")
            if isinstance(detail, dict)
            else "unknown",
            detail if isinstance(detail, dict) else {},
        )

    _emit_channel_ingress_telemetry(
        endpoint=endpoint,
        start=start,
        status_code=response.status_code,
        telemetry_emitter=telemetry_emitter,
        telemetry_context=telemetry_context,
    )
    response.raise_for_status()
    return {}


def channel_ingress_sync(
    *,
    base_url: str,
    api_version: str,
    client: httpx.Client,
    headers: Dict[str, str],
    event: Dict[str, Any],
    target: Optional[Dict[str, Any]] = None,
    input_data: Optional[Dict[str, Any]] = None,
    execution_config: Optional[Dict[str, Any]] = None,
    channel_type: Optional[str] = None,
    provider_key: Optional[str] = None,
    adapter_key: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    telemetry_emitter: Optional[TelemetryEmitter] = None,
    telemetry_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    endpoint = build_versioned_route(api_version, "/channels/ingress")
    payload = _build_channel_ingress_payload(
        event=event,
        target=target,
        input_data=input_data,
        execution_config=execution_config,
        channel_type=channel_type,
        provider_key=provider_key,
        adapter_key=adapter_key,
        idempotency_key=idempotency_key,
    )
    start = time.perf_counter()
    response = client.post(f"{base_url}{endpoint}", json=payload, headers=headers)
    return _handle_channel_ingress_response(
        endpoint=endpoint,
        response=response,
        start=start,
        telemetry_emitter=telemetry_emitter,
        telemetry_context=telemetry_context,
    )


async def channel_ingress_async(
    *,
    base_url: str,
    api_version: str,
    session: httpx.AsyncClient,
    headers: Dict[str, str],
    event: Dict[str, Any],
    target: Optional[Dict[str, Any]] = None,
    input_data: Optional[Dict[str, Any]] = None,
    execution_config: Optional[Dict[str, Any]] = None,
    channel_type: Optional[str] = None,
    provider_key: Optional[str] = None,
    adapter_key: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    telemetry_emitter: Optional[TelemetryEmitter] = None,
    telemetry_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    endpoint = build_versioned_route(api_version, "/channels/ingress")
    payload = _build_channel_ingress_payload(
        event=event,
        target=target,
        input_data=input_data,
        execution_config=execution_config,
        channel_type=channel_type,
        provider_key=provider_key,
        adapter_key=adapter_key,
        idempotency_key=idempotency_key,
    )
    start = time.perf_counter()
    response = await session.post(
        f"{base_url}{endpoint}",
        json=payload,
        headers=headers,
    )
    return _handle_channel_ingress_response(
        endpoint=endpoint,
        response=response,
        start=start,
        telemetry_emitter=telemetry_emitter,
        telemetry_context=telemetry_context,
    )
