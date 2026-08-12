# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""SDK agent event operation handlers."""

from __future__ import annotations

from typing import Any, Dict

import httpx

from atellagent_client.protocol.api import build_versioned_route


def emit_agent_event_sync(
    *,
    base_url: str,
    api_version: str,
    client: httpx.Client,
    headers: Dict[str, str],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Emit a canonical agent runtime event to the gateway.
    """
    url = f"{base_url}{build_versioned_route(api_version, '/agent-events/ingest')}"
    response = client.post(url, json=payload, headers=headers)
    data: Dict[str, Any]
    try:
        data = response.json()
    except Exception:
        data = {"message": response.text}
    return {
        "success": response.status_code == 200,
        "status": response.status_code,
        "data": data,
    }


async def emit_agent_event_async(
    *,
    base_url: str,
    api_version: str,
    session: httpx.AsyncClient,
    headers: Dict[str, str],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Emit a canonical agent runtime event to the gateway.
    """
    url = f"{base_url}{build_versioned_route(api_version, '/agent-events/ingest')}"
    response = await session.post(url, json=payload, headers=headers)
    data: Dict[str, Any]
    try:
        data = response.json()
    except Exception:
        data = {"message": response.text}
    return {
        "success": response.status_code == 200,
        "status": response.status_code,
        "data": data,
    }
