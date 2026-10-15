# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Model invocation submission and bounded response polling."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from atellagent_client.protocol.api import build_versioned_route
from atellagent_client.sdk.operations_modules.invocation_errors import (
    raise_forbidden_invocation_error,
)

_MODEL_INVOCATIONS_PATH = "/model-invocations"


def _normalize_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def invoke_model_sync(
    governance: Any,
    *,
    workflow_context: Dict[str, Any],
    payload: Dict[str, Any],
    poll_timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 0.5,
) -> Dict[str, Any]:
    url = (
        f"{governance.gateway_session.base_url}"
        f"{build_versioned_route(governance.config.api_version, _MODEL_INVOCATIONS_PATH)}"
    )
    response = governance._request_gateway_sync(
        "POST", url, workflow_context=workflow_context, json=payload
    )
    if response.status_code == 200:
        return response.json() if response.content else {}
    if response.status_code == 403:
        raise_forbidden_invocation_error(response.json() if response.content else {})
    if response.status_code != 202:
        detail = response.json() if response.content else {}
        raise RuntimeError(
            str(detail or f"model invocation failed with status {response.status_code}")
        )

    submission = response.json() if response.content else {}
    request_id = _normalize_optional_text(submission.get("request_id"))
    if not request_id:
        raise RuntimeError(
            "Missing request_id in async model invocation submission response"
        )
    poll_url = (
        f"{governance.gateway_session.base_url}"
        f"{build_versioned_route(governance.config.api_version, f'/model-invocations/responses/{request_id}')}"
    )
    deadline = time.perf_counter() + float(poll_timeout_seconds)
    while True:
        if time.perf_counter() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for model invocation result (request_id={request_id})"
            )
        poll_response = governance._request_gateway_sync(
            "GET", poll_url, workflow_context=workflow_context
        )
        poll_payload = poll_response.json() if poll_response.content else {}
        if poll_response.status_code == 202:
            time.sleep(max(0.1, float(poll_interval_seconds)))
            continue
        if poll_response.status_code == 200:
            result = poll_payload.get("result")
            return result if isinstance(result, dict) else {}
        if poll_response.status_code == 403:
            raise_forbidden_invocation_error(poll_payload, request_id=request_id)
        raise RuntimeError(
            str(
                poll_payload
                or f"model invocation polling failed with status {poll_response.status_code}"
            )
        )


async def invoke_model_async(
    governance: Any,
    *,
    workflow_context: Dict[str, Any],
    payload: Dict[str, Any],
    poll_timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 0.5,
) -> Dict[str, Any]:
    url = (
        f"{governance.gateway_session.base_url}"
        f"{build_versioned_route(governance.config.api_version, _MODEL_INVOCATIONS_PATH)}"
    )
    response = await governance._request_gateway_async(
        "POST", url, workflow_context=workflow_context, json=payload
    )
    if response.status_code == 200:
        return response.json() if response.content else {}
    if response.status_code == 403:
        raise_forbidden_invocation_error(response.json() if response.content else {})
    if response.status_code != 202:
        detail = response.json() if response.content else {}
        raise RuntimeError(
            str(detail or f"model invocation failed with status {response.status_code}")
        )

    submission = response.json() if response.content else {}
    request_id = _normalize_optional_text(submission.get("request_id"))
    if not request_id:
        raise RuntimeError(
            "Missing request_id in async model invocation submission response"
        )
    poll_url = (
        f"{governance.gateway_session.base_url}"
        f"{build_versioned_route(governance.config.api_version, f'/model-invocations/responses/{request_id}')}"
    )
    deadline = time.perf_counter() + float(poll_timeout_seconds)
    while True:
        if time.perf_counter() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for model invocation result (request_id={request_id})"
            )
        poll_response = await governance._request_gateway_async(
            "GET", poll_url, workflow_context=workflow_context
        )
        poll_payload = poll_response.json() if poll_response.content else {}
        if poll_response.status_code == 202:
            await asyncio.sleep(max(0.1, float(poll_interval_seconds)))
            continue
        if poll_response.status_code == 200:
            result = poll_payload.get("result")
            return result if isinstance(result, dict) else {}
        if poll_response.status_code == 403:
            raise_forbidden_invocation_error(poll_payload, request_id=request_id)
        raise RuntimeError(
            str(
                poll_payload
                or f"model invocation polling failed with status {poll_response.status_code}"
            )
        )
