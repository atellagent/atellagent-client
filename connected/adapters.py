# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Type adapters mounted on the single connected participant control plane."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Mapping

from atellagent_client.integrations.channels.registry import ChannelAdapterRegistry
from atellagent_client.integrations.models.contracts import (
    FilterRuntimeHandler,
    ModelRuntimeHandler,
    coerce_filter_runtime_evaluation_request,
    coerce_filter_runtime_result,
    coerce_model_runtime_invocation_request,
    coerce_model_runtime_result,
)
from atellagent_client.integrations.workflows.handlers import WorkflowParticipantHandler
from atellagent_client.integrations.workflows.contracts import (
    coerce_workflow_participant_cancel_request,
    coerce_workflow_participant_compile_request,
    coerce_workflow_participant_execute_request,
    coerce_workflow_participant_resume_request,
)
from atellagent_client.integrations.workflows.connected_actions import (
    _bind_connected_actions,
    _reset_connected_actions,
)

from .actions import ConnectedActionClient
from .contracts import ConnectedDelivery, ConnectedHandlerResult
from .participant import ConnectedParticipant


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _object(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return dict(value)


def _target_mode(*, consequential: bool, target_idempotent: bool) -> str:
    if consequential and not target_idempotent:
        raise ValueError(
            "consequential connected adapters require a target that honors the "
            "delivery idempotency key"
        )
    return "target" if target_idempotent else "none"


def _local_mcp_facts(tool_name: str, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    """Project only the documented bounded facts used by the free local PEP."""
    facts: Dict[str, Any] = {}
    for key in (
        "path",
        "access",
        "bytes",
        "results",
        "patch_hunks",
        "profile",
        "git_target",
    ):
        if key in arguments:
            facts[key] = arguments[key]
    if "access" not in facts:
        if tool_name == "file.read":
            facts["access"] = "read"
        elif tool_name in {"file.write", "file.patch"}:
            facts["access"] = "write"
    if "bytes" not in facts:
        content = next(
            (
                arguments[key]
                for key in ("content", "data", "text")
                if isinstance(arguments.get(key), str)
            ),
            None,
        )
        if content is not None:
            facts["bytes"] = len(content.encode("utf-8"))
    return facts


def mount_agent_handler(
    participant: ConnectedParticipant,
    handler: Callable[[Dict[str, Any]], Any],
    *,
    consequential: bool = True,
    target_idempotent: bool,
) -> None:
    async def invoke(
        delivery: ConnectedDelivery, _actions: ConnectedActionClient
    ) -> ConnectedHandlerResult:
        payload = dict(delivery.payload)
        communication = _object(
            payload.get("communication_metadata", {}), "communication_metadata"
        )
        communication_id = str(communication.get("communication_id") or "")
        if communication_id != delivery.idempotency_key:
            raise ValueError("agent communication idempotency binding mismatch")
        raw_result = await _await(handler(payload))
        if isinstance(raw_result, Mapping):
            result = dict(raw_result)
        elif raw_result is None:
            result = {"content": ""}
        else:
            result = {"content": str(raw_result)}
        if not isinstance(result.get("content"), str):
            candidate = result.get("message", result.get("response", ""))
            result["content"] = candidate if isinstance(candidate, str) else ""
        metadata = result.get("metadata", {})
        result["metadata"] = dict(metadata) if isinstance(metadata, Mapping) else {}
        return ConnectedHandlerResult.succeeded(
            result_schema="atellagent.connected.agent-result.v1",
            result_payload=result,
        )

    participant.register_handler(
        "agent.process",
        invoke,
        consequential=consequential,
        idempotency_mode=_target_mode(
            consequential=consequential, target_idempotent=target_idempotent
        ),
    )


def mount_model_handler(
    participant: ConnectedParticipant,
    handler: ModelRuntimeHandler,
    *,
    target_idempotent: bool,
) -> None:
    async def invoke(
        delivery: ConnectedDelivery, _actions: ConnectedActionClient
    ) -> ConnectedHandlerResult:
        envelope = _object(delivery.payload, "model delivery")
        request_payload = _object(envelope.get("input"), "model input")
        request_payload.setdefault("request_id", delivery.idempotency_key)
        request = coerce_model_runtime_invocation_request(request_payload)
        result = coerce_model_runtime_result(await handler.invoke_model(request))
        return ConnectedHandlerResult.succeeded(
            result_schema="atellagent.connected.model-result.v1",
            result_payload=result,
        )

    participant.register_handler(
        "model.invoke",
        invoke,
        consequential=True,
        idempotency_mode=_target_mode(
            consequential=True, target_idempotent=target_idempotent
        ),
    )


def mount_filter_handler(
    participant: ConnectedParticipant,
    handler: FilterRuntimeHandler,
    *,
    target_idempotent: bool,
) -> None:
    async def evaluate(
        delivery: ConnectedDelivery, _actions: ConnectedActionClient
    ) -> ConnectedHandlerResult:
        envelope = _object(delivery.payload, "filter delivery")
        request_payload = _object(envelope.get("input"), "filter input")
        request_payload.setdefault("request_id", delivery.idempotency_key)
        request = coerce_filter_runtime_evaluation_request(request_payload)
        result = coerce_filter_runtime_result(await handler.evaluate_filter(request))
        return ConnectedHandlerResult.succeeded(
            result_schema="atellagent.connected.ml-filter-result.v1",
            result_payload=result,
        )

    participant.register_handler(
        "filter.evaluate",
        evaluate,
        consequential=True,
        idempotency_mode=_target_mode(
            consequential=True, target_idempotent=target_idempotent
        ),
    )


def mount_channel_registry(
    participant: ConnectedParticipant,
    registry: ChannelAdapterRegistry,
    *,
    target_idempotent: bool,
) -> None:
    async def dispatch(
        delivery: ConnectedDelivery, _actions: ConnectedActionClient
    ) -> ConnectedHandlerResult:
        envelope = _object(delivery.payload, "channel delivery")
        if str(envelope.get("event_id") or "") != delivery.idempotency_key:
            raise ValueError("channel event idempotency binding mismatch")
        action = str(envelope.get("action") or "").strip().lower()
        if action == "ingress":
            channel = _object(envelope.get("channel"), "channel selector")
            registry.resolve(
                adapter_key=channel.get("adapter_key"),
                channel_type=channel.get("channel_type"),
                provider_key=channel.get("provider_key"),
            )
            result = {
                "status": "accepted",
                "event_id": delivery.idempotency_key,
            }
        else:
            result = await registry.dispatch_egress_action(envelope=envelope)
        return ConnectedHandlerResult.succeeded(
            result_schema="atellagent.connected.channel-result.v1",
            result_payload=result,
        )

    participant.register_handler(
        "channel.*",
        dispatch,
        consequential=True,
        idempotency_mode=_target_mode(
            consequential=True, target_idempotent=target_idempotent
        ),
    )


def mount_mcp_handler(
    participant: ConnectedParticipant,
    handler: Callable[[Dict[str, Any], str], Any],
    *,
    consequential: bool,
    target_idempotent: bool,
) -> None:
    """Mount a local MCP adapter that must accept the stable idempotency key."""

    async def invoke(
        delivery: ConnectedDelivery, _actions: ConnectedActionClient
    ) -> ConnectedHandlerResult:
        request = _object(delivery.payload.get("request"), "MCP request")
        params = _object(request.get("params"), "MCP tools/call params")
        tool_name = str(params.get("name") or "").strip()
        arguments = _object(params.get("arguments", {}), "MCP tool arguments")
        if not tool_name:
            raise ValueError("MCP tools/call requires a tool name")
        await participant.enforce_local_action(
            action=tool_name,
            correlation_id=delivery.message_id,
            facts=_local_mcp_facts(tool_name, arguments),
        )
        response = await _await(handler(request, delivery.idempotency_key))
        return ConnectedHandlerResult.succeeded(
            result_schema="atellagent.connected.mcp-result.v1",
            result_payload={"mcp_response": _object(response, "MCP response")},
        )

    participant.register_handler(
        "mcp.*",
        invoke,
        consequential=consequential,
        idempotency_mode=_target_mode(
            consequential=consequential, target_idempotent=target_idempotent
        ),
    )


def _workflow_request(delivery: ConnectedDelivery) -> Dict[str, Any]:
    payload = dict(delivery.payload)
    if delivery.operation in {"workflow.execute", "workflow.resume"}:
        payload.setdefault(
            "attempt_id", delivery.execution_attempt_id or delivery.idempotency_key
        )
        payload.setdefault("request_id", delivery.idempotency_key)
    elif delivery.operation == "workflow.cancel":
        payload.setdefault(
            "attempt_id", delivery.execution_attempt_id or delivery.idempotency_key
        )
    return payload


def _workflow_outcome(result: Any) -> ConnectedHandlerResult:
    value = _object(result, "workflow result")
    status = str(value.get("status") or "").strip().lower()
    if status in {"completed", "compiled"}:
        outcome = "completed"
        terminal = "succeeded"
    elif status in {"cancelled", "cancellation_requested"}:
        outcome = "cancelled"
        terminal = "cancelled"
    elif status == "failed":
        outcome = "failed"
        terminal = "failed"
    elif status == "suspended":
        outcome = "suspended"
        terminal = "succeeded"
    else:
        raise ValueError("workflow handler returned an unsupported status")
    output = value.get("output", {})
    if not isinstance(output, Mapping):
        output = {"result": output}
    payload: Dict[str, Any] = {
        "protocol_version": "v1",
        "outcome": outcome,
        "output": dict(output),
        "error": None,
        "wait": None,
    }
    if outcome == "failed":
        error = value.get("error")
        payload["error"] = (
            dict(error) if isinstance(error, Mapping) else {"detail": str(error or "failed")}
        )
    if outcome == "suspended":
        wait = value.get("wait")
        if not isinstance(wait, Mapping):
            raise ValueError("suspended workflow result requires a complete wait object")
        payload["wait"] = dict(wait)
    execution_time_ms = value.get("execution_time_ms")
    if execution_time_ms is not None:
        payload["execution_time_ms"] = int(execution_time_ms)
    return ConnectedHandlerResult(
        terminal_status=terminal,
        result_schema="atellagent.connected.workflow-outcome.v1",
        result_payload=payload,
    )


def mount_workflow_handler(
    participant: ConnectedParticipant,
    handler: WorkflowParticipantHandler,
    *,
    supports_compile: bool = True,
    supports_continuations: bool = True,
    supports_cancel: bool = True,
    target_idempotent: bool,
) -> None:
    async def dispatch(
        delivery: ConnectedDelivery, actions: ConnectedActionClient
    ) -> ConnectedHandlerResult:
        request = _workflow_request(delivery)
        action_token = _bind_connected_actions(actions)
        try:
            if delivery.operation == "workflow.compile" and supports_compile:
                result = await handler.compile(
                    coerce_workflow_participant_compile_request(request)
                )
            elif delivery.operation == "workflow.execute":
                result = await handler.execute(
                    coerce_workflow_participant_execute_request(request)
                )
            elif delivery.operation == "workflow.resume" and supports_continuations:
                result = await handler.resume(
                    coerce_workflow_participant_resume_request(request)
                )
            elif delivery.operation == "workflow.cancel" and supports_cancel:
                result = await handler.cancel(
                    coerce_workflow_participant_cancel_request(request)
                )
            else:
                raise ValueError("workflow operation is not supported by this participant")
            return _workflow_outcome(result)
        except Exception as exc:
            return ConnectedHandlerResult(
                terminal_status="failed",
                result_schema="atellagent.connected.workflow-outcome.v1",
                result_payload={
                    "protocol_version": "v1",
                    "outcome": "failed",
                    "output": {},
                    "error": {"type": type(exc).__name__, "detail": str(exc)},
                    "wait": None,
                },
            )
        finally:
            _reset_connected_actions(action_token)

    operations = ["workflow.execute"]
    if supports_compile:
        operations.append("workflow.compile")
    if supports_continuations:
        operations.append("workflow.resume")
    if supports_cancel:
        operations.append("workflow.cancel")
    mode = _target_mode(consequential=True, target_idempotent=target_idempotent)
    for operation in operations:
        participant.register_handler(
            operation,
            dispatch,
            consequential=True,
            idempotency_mode=mode,
        )


__all__ = [
    "mount_agent_handler",
    "mount_channel_registry",
    "mount_filter_handler",
    "mount_mcp_handler",
    "mount_model_handler",
    "mount_workflow_handler",
]
