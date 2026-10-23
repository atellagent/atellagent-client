# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under LICENSE.md in this source tree.
"""Direct MCP authorization over the participant's existing enrolled session."""

import asyncio
import json
from collections.abc import Mapping

from atellagent_client.governance.actions import ActionDenied, PolicyActionDenied, RuntimeActionGate
from atellagent_client.pep.gateway import GatewayDirectiveVerifier
from .mcp_handler import execute_mcp_handler, prepare_mcp_request


async def _boundary_request(participant, phase, payload):
    try:
        async with asyncio.timeout(35):
            response = await participant._request("POST", f"/v1/mcp/boundary/{phase}", json=payload)
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except asyncio.CancelledError:
        raise
    except Exception:
        # Gateway/provider details can contain protected content. Return only a
        # fixed failure; a failed or uncertain request never permits execution.
        raise ActionDenied(f"mcp_{phase}_unavailable") from None


async def handle_direct_mcp_request(participant, request, *, idempotency_key, caller=None):
    if (participant.config.integration_type != "mcp"
            or participant.config.control_source != "cluster_directive"
            or not participant._started or participant._stop_event.is_set()
            or participant._instance_id is None or participant._mcp_handler is None):
        raise ActionDenied("mcp_runtime_unavailable")
    if (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 255
            or idempotency_key != idempotency_key.strip()):
        raise ActionDenied("mcp_idempotency_key_invalid")
    prepared = prepare_mcp_request(participant, request)
    if caller is not None and not isinstance(caller, Mapping):
        raise ActionDenied("mcp_caller_invalid")
    try:
        summary = None if caller is None else json.loads(json.dumps(dict(caller), allow_nan=False))
    except (TypeError, ValueError):
        raise ActionDenied("mcp_caller_invalid") from None
    wire = {"tool_name": prepared["params"]["name"],
        "arguments": json.loads(json.dumps(prepared["params"]["arguments"])),
        "idempotency_key": idempotency_key, "caller": summary}
    async with participant._semaphore:
        if participant._stop_event.is_set():
            raise ActionDenied("mcp_runtime_unavailable")
        receipt = await _boundary_request(participant, "preflight", wire)
        decision = receipt.get("decision")
        key = receipt.get("action_key")
        if (receipt.get("success") is not True or not isinstance(decision, dict)
                or decision.get("allowed") is not True or not isinstance(key, str) or not key
                or not isinstance(decision.get("decision_id"), str) or not decision["decision_id"]):
            raise PolicyActionDenied("mcp_action_not_authorized")
        publication = participant._mcp_publication
        if publication is None:
            raise ActionDenied("mcp_publication_unavailable")
        if participant._mcp_direct_gate is None:
            participant._mcp_direct_gate = RuntimeActionGate(source="cluster_directive",
                directive_verifier=GatewayDirectiveVerifier(participant.config,
                    http_client_manager=participant.session.http_client_manager))
        await participant._mcp_direct_gate.enforce(action=wire["tool_name"], integration_type="mcp",
            correlation_id=key, encoded_directive=receipt.get("control_directive"), facts=wire["arguments"],
            workflow_context={"tenant_id": participant.config.tenant_id},
            policy_decision_id=decision["decision_id"], descriptor_binding={
                "id": f"mcp:{participant.config.integration_id}",
                "revision": publication["descriptor_revision"], "digest": publication["manifest_hash"],
                "action_id": f"{participant.config.service_account_id}:{wire['tool_name']}",
                "execution_route": "governed_mcp", "integration_id": participant.config.integration_id})
        try:
            result = await execute_mcp_handler(participant, participant._mcp_handler, prepared, key)
        except asyncio.CancelledError:
            raise
        except Exception:
            await _boundary_request(participant, "postflight", {**wire, "action_key": key,
                "success": False, "error_type": "mcp_handler_failed"})
            raise ActionDenied("mcp_handler_failed") from None
        content = result.get("result", result)
        succeeded = "error" not in result and not (isinstance(content, dict) and content.get("isError") is True)
        settled = await _boundary_request(participant, "postflight", {**wire, "action_key": key,
            "success": succeeded, "result_payload": result})
        response_decision = settled.get("decision")
        if (settled.get("success") is not True or settled.get("action_key") != key
                or not isinstance(response_decision, dict) or response_decision.get("allowed") is not True):
            raise PolicyActionDenied("mcp_response_not_authorized")
        return result
