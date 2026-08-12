# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""SDK MCP communication operation handlers."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

import httpx

from atellagent_client.protocol.api import build_versioned_route
from atellagent_client.sdk.errors import PolicyViolationError
from atellagent_client.sdk.telemetry import TelemetryEmitter, TelemetryEvent
from .common import extract_policy_detail

logger = logging.getLogger(__name__)


def _policy_violation_message(detail: Dict[str, Any]) -> str:
    raw_message = (
        detail.get("message")
        or detail.get("error")
        or detail.get("detail")
        or "MCP tool call blocked by policy"
    )
    if isinstance(raw_message, str):
        message = raw_message.strip()
        if message:
            return message
    return "MCP tool call blocked by policy"


def _raise_policy_violation(detail_payload: Any) -> None:
    detail = detail_payload if isinstance(detail_payload, dict) else {}
    violation_type_raw = detail.get("violation_type")
    violation_type = (
        violation_type_raw.strip()
        if isinstance(violation_type_raw, str) and violation_type_raw.strip()
        else "unknown"
    )
    raise PolicyViolationError(
        _policy_violation_message(detail),
        violation_type,
        detail,
    )


def _extract_error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload) if payload is not None else "MCP communication failed"

    detail = payload.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if isinstance(detail, dict):
        for key in ("message", "error", "detail"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(detail, default=str)

    for key in ("error", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = _extract_error_message(value)
            if nested:
                return nested
    return json.dumps(payload, default=str)


def _raise_mcp_failure(
    *,
    communication_id: Optional[str],
    status_code: int,
    payload: Any,
) -> None:
    message = _extract_error_message(payload)
    if communication_id:
        raise RuntimeError(
            "MCP communication polling failed "
            f"(communication_id={communication_id}, status={status_code}): {message}"
        )
    raise RuntimeError(
        "MCP communication request failed "
        f"(status={status_code}): {message}"
    )


def mcp_communicate_sync(
    *,
    base_url: str,
    api_version: str,
    client: httpx.Client,
    headers: Dict[str, str],
    source_agent: str,
    target_agent: str,
    tool_name: str,
    arguments: Dict[str, Any],
    task_type: str = "mcp_tool",
    priority: str = "normal",
    tool_call_id: Optional[str] = None,
    action_context: Optional[Dict[str, Any]] = None,
    poll_timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 0.2,
    telemetry_emitter: Optional[TelemetryEmitter] = None,
    telemetry_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Invoke an MCP tool through the gateway (sync).
    """
    endpoint = build_versioned_route(api_version, "/mcp/communicate")
    url = f"{base_url}{endpoint}"
    payload = {
        "source_agent": source_agent,
        "target_agent": target_agent,
        "message": json.dumps(
            {"tool_name": tool_name, "tool_call_id": tool_call_id, "arguments": arguments}
        ),
        "task_type": task_type,
        "context": {
            **(dict(action_context or {})),
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
        },
        "priority": priority,
    }

    start = time.perf_counter()
    response = client.post(url, json=payload, headers=headers)
    final_status = response.status_code

    def _emit_telemetry(
        *,
        status_code: int,
        policy_result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        if telemetry_emitter:
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

    if response.status_code == 202:
        try:
            submission_data = response.json()
        except Exception:
            submission_data = {}
        communication_id = submission_data.get("communication_id")
        if not communication_id:
            raise RuntimeError("Missing communication_id in MCP submission response")

        poll_url = (
            f"{base_url}"
            f"{build_versioned_route(api_version, f'/mcp/communicate/responses/{communication_id}')}"
        )
        deadline = time.perf_counter() + float(poll_timeout_seconds)
        interval_seconds = max(0.1, float(poll_interval_seconds))

        while True:
            if time.perf_counter() >= deadline:
                raise TimeoutError(
                    "Timed out waiting for MCP communication result "
                    f"(communication_id={communication_id})"
                )
            poll_response = client.get(poll_url, headers=headers)
            final_status = poll_response.status_code
            if poll_response.status_code == 202:
                time.sleep(interval_seconds)
                continue

            poll_data: Dict[str, Any] = {}
            if poll_response.content:
                try:
                    poll_data = poll_response.json()
                except Exception:
                    poll_data = {"detail": poll_response.text}
            if poll_response.status_code == 200:
                _emit_telemetry(status_code=final_status)
                return poll_data if isinstance(poll_data, dict) else {}
            if poll_response.status_code == 403:
                detail = extract_policy_detail(poll_data)
                _emit_telemetry(
                    status_code=final_status,
                    policy_result=detail or None,
                    error_message=_policy_violation_message(detail)
                    if isinstance(detail, dict)
                    else "MCP tool call blocked by policy",
                )
                _raise_policy_violation(detail)
            _emit_telemetry(status_code=final_status)
            _raise_mcp_failure(
                communication_id=str(communication_id),
                status_code=poll_response.status_code,
                payload=poll_data,
            )

    if response.status_code == 200:
        data = response.json()
        _emit_telemetry(status_code=final_status)
        return data
    if response.status_code == 403:
        error_data = {}
        try:
            error_data = response.json()
        except Exception:
            error_data = {}
        detail = extract_policy_detail(error_data)
        _emit_telemetry(
            status_code=final_status,
            policy_result=detail or None,
            error_message=_policy_violation_message(detail)
            if isinstance(detail, dict)
            else "MCP tool call blocked by policy",
        )
        _raise_policy_violation(detail)

    _emit_telemetry(status_code=final_status)
    error_data: Any = {}
    if response.content:
        try:
            error_data = response.json()
        except Exception:
            error_data = {"detail": response.text}
    _raise_mcp_failure(
        communication_id=None,
        status_code=response.status_code,
        payload=error_data,
    )


async def mcp_communicate_async(
    *,
    base_url: str,
    api_version: str,
    session: httpx.AsyncClient,
    headers: Dict[str, str],
    source_agent: str,
    target_agent: str,
    tool_name: str,
    arguments: Dict[str, Any],
    task_type: str = "mcp_tool",
    priority: str = "normal",
    tool_call_id: Optional[str] = None,
    action_context: Optional[Dict[str, Any]] = None,
    poll_timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 0.2,
    telemetry_emitter: Optional[TelemetryEmitter] = None,
    telemetry_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Invoke an MCP tool through the gateway (async).
    """
    endpoint = build_versioned_route(api_version, "/mcp/communicate")
    url = f"{base_url}{endpoint}"
    payload = {
        "source_agent": source_agent,
        "target_agent": target_agent,
        "message": json.dumps(
            {"tool_name": tool_name, "tool_call_id": tool_call_id, "arguments": arguments}
        ),
        "task_type": task_type,
        "context": {
            **(dict(action_context or {})),
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
        },
        "priority": priority,
    }

    start = time.perf_counter()
    response = await session.post(url, json=payload, headers=headers)
    final_status = response.status_code

    def _emit_telemetry(
        *,
        status_code: int,
        policy_result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        if telemetry_emitter:
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

    if response.status_code == 202:
        try:
            submission_data = response.json()
        except Exception:
            submission_data = {}
        communication_id = submission_data.get("communication_id")
        if not communication_id:
            raise RuntimeError("Missing communication_id in MCP submission response")

        poll_url = (
            f"{base_url}"
            f"{build_versioned_route(api_version, f'/mcp/communicate/responses/{communication_id}')}"
        )
        deadline = time.perf_counter() + float(poll_timeout_seconds)
        interval_seconds = max(0.1, float(poll_interval_seconds))

        while True:
            if time.perf_counter() >= deadline:
                raise TimeoutError(
                    "Timed out waiting for MCP communication result "
                    f"(communication_id={communication_id})"
                )
            poll_response = await session.get(poll_url, headers=headers)
            final_status = poll_response.status_code
            if poll_response.status_code == 202:
                await asyncio.sleep(interval_seconds)
                continue

            poll_data: Any = {}
            if poll_response.content:
                try:
                    poll_data = poll_response.json()
                except Exception:
                    poll_data = {"detail": poll_response.text}
            if poll_response.status_code == 200:
                _emit_telemetry(status_code=final_status)
                return poll_data if isinstance(poll_data, dict) else {}
            if poll_response.status_code == 403:
                detail = extract_policy_detail(poll_data)
                _emit_telemetry(
                    status_code=final_status,
                    policy_result=detail or None,
                    error_message=_policy_violation_message(detail)
                    if isinstance(detail, dict)
                    else "MCP tool call blocked by policy",
                )
                _raise_policy_violation(detail)
            _emit_telemetry(status_code=final_status)
            _raise_mcp_failure(
                communication_id=str(communication_id),
                status_code=poll_response.status_code,
                payload=poll_data,
            )

    if response.status_code == 200:
        data = response.json()
        _emit_telemetry(status_code=final_status)
        return data
    if response.status_code == 403:
        error_data = {}
        try:
            error_data = response.json()
        except Exception:
            error_data = {}
        detail = extract_policy_detail(error_data)
        _emit_telemetry(
            status_code=final_status,
            policy_result=detail or None,
            error_message=_policy_violation_message(detail)
            if isinstance(detail, dict)
            else "MCP tool call blocked by policy",
        )
        _raise_policy_violation(detail)

    _emit_telemetry(status_code=final_status)
    error_data: Any = {}
    if response.content:
        try:
            error_data = response.json()
        except Exception:
            error_data = {"detail": response.text}
    _raise_mcp_failure(
        communication_id=None,
        status_code=response.status_code,
        payload=error_data,
    )
