# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under LICENSE.md in this source tree.
"""The mounted MCP tool path shared by connected and direct intakes."""

import inspect
import json
from collections.abc import Mapping

from atellagent_client.governance.actions import ActionDenied


def prepare_mcp_request(participant, request):
    # Snapshot before any await: caller mutation cannot change authorized args.
    if not isinstance(request, Mapping):
        raise ActionDenied("mcp_request_invalid")
    try:
        prepared = json.loads(json.dumps(dict(request), allow_nan=False))
    except (TypeError, ValueError):
        raise ActionDenied("mcp_request_invalid") from None
    params = prepared.get("params")
    if (prepared.get("jsonrpc") != "2.0" or prepared.get("method") != "tools/call"
            or not isinstance(params, dict) or not isinstance(params.get("name"), str)
            or not params["name"] or params["name"] != params["name"].strip()
            or not isinstance(params.get("arguments", {}), dict)):
        raise ActionDenied("mcp_request_invalid")
    metadata = params.get("_meta", {})
    if not isinstance(metadata, dict) or any(
            str(key).lower().startswith("atellagent/") and key != "atellagent/idempotencyKey"
            for key in metadata):
        raise ActionDenied("mcp_authority_intake_mismatch")
    tools = (participant._mcp_manifest or {}).get("tools", [])
    if not any(isinstance(tool, dict) and tool.get("name") == params["name"] for tool in tools):
        raise ActionDenied("mcp_tool_not_published")
    params.setdefault("arguments", {})
    return prepared


async def execute_mcp_handler(participant, handler, request, idempotency_key, *, correlation_id=None):
    # Both paths authorize before entering this common local execution step.
    from .adapters import _local_mcp_facts
    params = request["params"]
    await participant.enforce_local_action(action=params["name"], correlation_id=correlation_id or idempotency_key,
        facts=_local_mcp_facts(params["name"], params["arguments"]))
    result = handler(request, idempotency_key)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, Mapping):
        raise ActionDenied("mcp_response_invalid")
    try:
        return json.loads(json.dumps(dict(result), allow_nan=False))
    except (TypeError, ValueError):
        raise ActionDenied("mcp_response_invalid") from None
