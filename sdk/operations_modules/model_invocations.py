# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""SDK provider-neutral model invocation operation handlers."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Dict, List, Optional

import httpx

from atellagent_client.protocol.api import build_versioned_route
from atellagent_client.sdk.telemetry import TelemetryEmitter, TelemetryEvent
from .invocation_errors import (
    error_kind_from_payload,
    error_message_from_payload,
    raise_forbidden_invocation_error,
)
from .common import sanitize_model_invocation_options

logger = logging.getLogger(__name__)


def _build_poll_failure_message(
    *,
    request_id: str,
    status_code: int,
) -> str:
    return (
        f"Model invocation polling failed (request_id={request_id}, "
        f"status={status_code}). Check backend logs for request_id details."
    )


def _build_poll_failure_message_with_payload(
    *,
    request_id: str,
    status_code: int,
    payload: Any,
) -> str:
    error_kind = error_kind_from_payload(payload)
    error_message = error_message_from_payload(payload)
    if error_kind:
        return (
            f"Model invocation polling failed (request_id={request_id}, "
            f"status={status_code}, error_kind={error_kind}): {error_message}"
        )
    return (
        f"Model invocation polling failed (request_id={request_id}, "
        f"status={status_code}): {error_message}"
    )


def _poll_sleep_seconds(
    *,
    attempt: int,
    base_interval: float,
    max_interval: float,
    jitter_ratio: float,
) -> float:
    capped_attempt = max(0, attempt)
    interval = min(max_interval, base_interval * (1.5**capped_attempt))
    if jitter_ratio > 0:
        interval *= 1.0 + random.uniform(-jitter_ratio, jitter_ratio)
    return max(0.05, interval)


def model_invocation_sync(
    *,
    base_url: str,
    api_version: str,
    client: httpx.Client,
    headers: Dict[str, str],
    messages: List[Dict[str, Any]],
    stream: bool = False,
    telemetry_emitter: Optional[TelemetryEmitter] = None,
    telemetry_context: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Send a model invocation request (sync)."""
    poll_timeout_seconds = float(kwargs.pop("poll_timeout_seconds", 300))
    poll_interval_seconds = max(0.1, float(kwargs.pop("poll_interval_seconds", 0.5)))
    poll_max_interval_seconds = max(
        poll_interval_seconds,
        float(kwargs.pop("poll_max_interval_seconds", 2.0)),
    )
    poll_jitter_ratio = min(
        0.5,
        max(0.0, float(kwargs.pop("poll_jitter_ratio", 0.15))),
    )
    invocation_options = sanitize_model_invocation_options(kwargs)
    endpoint = build_versioned_route(api_version, "/model-invocations")
    url = f"{base_url}{endpoint}"
    payload = {
        "messages": messages,
        "stream": stream,
        **invocation_options,
    }

    start = time.perf_counter()
    response = client.post(url, json=payload, headers=headers)

    final_status = response.status_code
    data: Dict[str, Any] = {}

    if response.status_code == 202:
        try:
            submission_data = response.json()
        except Exception:
            submission_data = {}
        request_id = submission_data.get("request_id")
        if not request_id:
            raise RuntimeError(
                "Missing request_id in async model invocation submission response"
            )

        poll_url = (
            f"{base_url}"
            f"{build_versioned_route(api_version, f'/model-invocations/responses/{request_id}')}"
        )
        deadline = time.perf_counter() + poll_timeout_seconds
        poll_attempt = 0
        while True:
            if time.perf_counter() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for model invocation result (request_id={request_id})"
                )
            poll_response = client.get(poll_url, headers=headers)
            final_status = poll_response.status_code
            if poll_response.status_code == 202:
                time.sleep(
                    _poll_sleep_seconds(
                        attempt=poll_attempt,
                        base_interval=poll_interval_seconds,
                        max_interval=poll_max_interval_seconds,
                        jitter_ratio=poll_jitter_ratio,
                    )
                )
                poll_attempt += 1
                continue
            poll_data: Dict[str, Any] = {}
            if poll_response.content:
                try:
                    poll_data = poll_response.json()
                except Exception:
                    poll_data = {}
            if poll_response.status_code == 200:
                result = poll_data.get("result")
                data = result if isinstance(result, dict) else {}
                break
            if poll_response.status_code == 403:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                if telemetry_emitter:
                    try:
                        telemetry_emitter(
                            TelemetryEvent(
                                **(telemetry_context or {}),
                                method="POST",
                                endpoint=endpoint,
                                status_code=poll_response.status_code,
                                response_time_ms=elapsed_ms,
                                policy_result=poll_data if isinstance(poll_data, dict) else None,
                            )
                        )
                    except Exception:
                        logger.debug("telemetry emission failed", exc_info=True)
                raise_forbidden_invocation_error(poll_data, request_id=str(request_id))
            if poll_response.status_code >= 400:
                raise RuntimeError(
                    _build_poll_failure_message_with_payload(
                        request_id=request_id,
                        status_code=poll_response.status_code,
                        payload=poll_data,
                    )
                )
            poll_response.raise_for_status()
    elif response.status_code == 200:
        data = response.json()
    elif response.status_code == 403:
        error_data = (
            response.json()
            if response.headers.get("content-type", "").startswith(
                "application/json"
            )
            else {}
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        if telemetry_emitter:
            try:
                telemetry_emitter(
                    TelemetryEvent(
                        **(telemetry_context or {}),
                        method="POST",
                        endpoint=endpoint,
                        status_code=response.status_code,
                        response_time_ms=elapsed_ms,
                        policy_result=error_data or None,
                    )
                )
            except Exception:
                logger.debug("telemetry emission failed", exc_info=True)
        raise_forbidden_invocation_error(error_data)
    else:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        if telemetry_emitter:
            try:
                telemetry_emitter(
                    TelemetryEvent(
                        **(telemetry_context or {}),
                        method="POST",
                        endpoint=endpoint,
                        status_code=response.status_code,
                        response_time_ms=elapsed_ms,
                    )
                )
            except Exception:
                logger.debug("telemetry emission failed", exc_info=True)
        response.raise_for_status()

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    usage = data.get("usage") if isinstance(data, dict) else None
    tokens_used = usage.get("total_tokens") if isinstance(usage, dict) else None
    if telemetry_emitter:
        try:
            telemetry_emitter(
                TelemetryEvent(
                    **(telemetry_context or {}),
                    method="POST",
                    endpoint=endpoint,
                    status_code=final_status,
                    response_time_ms=elapsed_ms,
                    tokens_used=tokens_used,
                )
            )
        except Exception:
            logger.debug("telemetry emission failed", exc_info=True)
    return data


async def model_invocation_async(
    *,
    base_url: str,
    api_version: str,
    session: httpx.AsyncClient,
    headers: Dict[str, str],
    messages: List[Dict[str, Any]],
    stream: bool = False,
    telemetry_emitter: Optional[TelemetryEmitter] = None,
    telemetry_context: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Send a model invocation request (async)."""
    poll_timeout_seconds = float(kwargs.pop("poll_timeout_seconds", 300))
    poll_interval_seconds = max(0.1, float(kwargs.pop("poll_interval_seconds", 0.5)))
    poll_max_interval_seconds = max(
        poll_interval_seconds,
        float(kwargs.pop("poll_max_interval_seconds", 2.0)),
    )
    poll_jitter_ratio = min(
        0.5,
        max(0.0, float(kwargs.pop("poll_jitter_ratio", 0.15))),
    )
    invocation_options = sanitize_model_invocation_options(kwargs)
    endpoint = build_versioned_route(api_version, "/model-invocations")
    url = f"{base_url}{endpoint}"
    payload = {
        "messages": messages,
        "stream": stream,
        **invocation_options,
    }

    start = time.perf_counter()
    response = await session.post(url, json=payload, headers=headers)
    final_status = response.status_code
    data: Dict[str, Any] = {}
    if response.status_code == 202:
        try:
            submission_data = response.json()
        except Exception:
            submission_data = {}
        request_id = submission_data.get("request_id")
        if not request_id:
            raise RuntimeError(
                "Missing request_id in async model invocation submission response"
            )
        poll_url = (
            f"{base_url}"
            f"{build_versioned_route(api_version, f'/model-invocations/responses/{request_id}')}"
        )
        deadline = time.perf_counter() + poll_timeout_seconds
        poll_attempt = 0
        while True:
            if time.perf_counter() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for model invocation result (request_id={request_id})"
                )
            poll_response = await session.get(poll_url, headers=headers)
            final_status = poll_response.status_code
            if poll_response.status_code == 202:
                await asyncio.sleep(
                    _poll_sleep_seconds(
                        attempt=poll_attempt,
                        base_interval=poll_interval_seconds,
                        max_interval=poll_max_interval_seconds,
                        jitter_ratio=poll_jitter_ratio,
                    )
                )
                poll_attempt += 1
                continue
            poll_data = poll_response.json() if poll_response.content else {}
            if poll_response.status_code == 200:
                result = poll_data.get("result")
                data = result if isinstance(result, dict) else {}
                break
            if poll_response.status_code == 403:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                if telemetry_emitter:
                    try:
                        telemetry_emitter(
                            TelemetryEvent(
                                **(telemetry_context or {}),
                                method="POST",
                                endpoint=endpoint,
                                status_code=poll_response.status_code,
                                response_time_ms=elapsed_ms,
                                policy_result=poll_data if isinstance(poll_data, dict) else None,
                            )
                        )
                    except Exception:
                        logger.debug("telemetry emission failed", exc_info=True)
                raise_forbidden_invocation_error(poll_data, request_id=str(request_id))
            if poll_response.status_code >= 400:
                raise RuntimeError(
                    _build_poll_failure_message_with_payload(
                        request_id=request_id,
                        status_code=poll_response.status_code,
                        payload=poll_data,
                    )
                )
            poll_response.raise_for_status()
    elif response.status_code == 200:
        data = response.json()
    elif response.status_code == 403:
        try:
            error_data = response.json()
        except Exception:
            error_data = {}
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        if telemetry_emitter:
            try:
                telemetry_emitter(
                    TelemetryEvent(
                        **(telemetry_context or {}),
                        method="POST",
                        endpoint=endpoint,
                        status_code=response.status_code,
                        response_time_ms=elapsed_ms,
                        policy_result=error_data or None,
                    )
                )
            except Exception:
                logger.debug("telemetry emission failed", exc_info=True)
        raise_forbidden_invocation_error(error_data)
    else:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        if telemetry_emitter:
            try:
                telemetry_emitter(
                    TelemetryEvent(
                        **(telemetry_context or {}),
                        method="POST",
                        endpoint=endpoint,
                        status_code=response.status_code,
                        response_time_ms=elapsed_ms,
                    )
                )
            except Exception:
                logger.debug("telemetry emission failed", exc_info=True)
        response.raise_for_status()

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    usage = data.get("usage") if isinstance(data, dict) else None
    tokens_used = usage.get("total_tokens") if isinstance(usage, dict) else None
    if telemetry_emitter:
        try:
            telemetry_emitter(
                TelemetryEvent(
                    **(telemetry_context or {}),
                    method="POST",
                    endpoint=endpoint,
                    status_code=final_status,
                    response_time_ms=elapsed_ms,
                    tokens_used=tokens_used,
                )
            )
        except Exception:
            logger.debug("telemetry emission failed", exc_info=True)
    return data
