# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Customer-owned LangGraph adapter for the public workflow participant v1 protocol."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .handlers import WorkflowParticipantHandlerBase
from .types import (
    WorkflowParticipantCancelRequest,
    WorkflowParticipantCompileRequest,
    WorkflowParticipantExecutionRequest,
    WorkflowParticipantResult,
    WorkflowParticipantResumeRequest,
)


def _langgraph_command(*, resume: Any, update: Optional[Dict[str, Any]] = None) -> Any:
    try:
        from langgraph.types import Command
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph workflow support requires: "
            "pip install 'atellagent-client[langgraph]'"
        ) from exc
    return Command(resume=resume, update=update)


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _interrupt_wait_request(output: Any) -> Dict[str, Any]:
    """Return only the public approval fields supplied by a LangGraph interrupt."""
    if not isinstance(output, dict):
        return {}
    interrupt = output.get("__interrupt__")
    if isinstance(interrupt, (list, tuple)):
        interrupt = interrupt[0] if interrupt else None
    value = getattr(interrupt, "value", interrupt)
    source = _mapping(value)
    return {
        key: source[key]
        for key in ("title", "message", "description", "expires_at", "metadata")
        if key in source
    }


@dataclass
class LangGraphWorkflowParticipant(WorkflowParticipantHandlerBase):
    """Run a customer-compiled LangGraph at the participant boundary.

    A graph must be compiled with a checkpointer if it uses ``interrupt()``
    and therefore needs resume support. The adapter deliberately never
    evaluates graph source from ``workflow_definition``; the customer supplies
    the graph object when their runtime starts.
    """

    graph: Any
    runtime_type: str = "langgraph"
    interrupt_wait_boundary_type: str = "human_approval"
    _active_tasks: Dict[str, asyncio.Task[Any]] = field(default_factory=dict, init=False)
    _cancelled_execution_ids: set[str] = field(default_factory=set, init=False)

    def _config(self, payload: WorkflowParticipantExecutionRequest) -> Dict[str, Any]:
        return {
            "configurable": {
                "thread_id": str(payload["execution_id"]),
                "atellagent_execution_id": str(payload["execution_id"]),
                "atellagent_deployment_id": str(payload["deployment_id"]),
                "atellagent_attempt_id": str(payload["attempt_id"]),
                "atellagent_workflow_context": _mapping(payload.get("workflow_context")),
            }
        }

    @staticmethod
    def _result(
        *,
        status: str,
        payload: WorkflowParticipantExecutionRequest,
        output: Any = None,
    ) -> WorkflowParticipantResult:
        result: WorkflowParticipantResult = {
            "status": status,
            "runtime_type": "langgraph",
            "execution_id": str(payload["execution_id"]),
            "deployment_id": str(payload["deployment_id"]),
            "attempt_id": str(payload["attempt_id"]),
        }
        if output is not None:
            result["output"] = output
        return result

    def _assert_runtime_type(self, runtime_type: Any) -> None:
        if str(runtime_type or "").strip().lower() != self.runtime_type:
            raise ValueError(
                "LangGraph workflow participant requires "
                f"runtime_type={self.runtime_type!r}"
            )

    async def _suspend_for_interrupt(
        self,
        *,
        payload: WorkflowParticipantExecutionRequest,
        output: Any,
    ) -> WorkflowParticipantResult:
        boundary_type = str(self.interrupt_wait_boundary_type or "").strip().lower()
        if boundary_type != "human_approval":
            raise ValueError(
                "LangGraph interrupt_wait_boundary_type must be 'human_approval'"
            )
        execution_id = str(payload["execution_id"])
        attempt_id = str(payload["attempt_id"])
        wait_id = f"langgraph:{execution_id}:{attempt_id}"
        wait = {
            "wait_id": wait_id,
            "wait_boundary_type": boundary_type,
            "continuation": {"runtime_type": self.runtime_type},
            "wait_request": _interrupt_wait_request(output),
            "idempotency_key": f"{attempt_id}:langgraph-interrupt",
        }
        result = self._result(status="suspended", payload=payload)
        result["wait_id"] = wait_id
        result["wait_boundary_type"] = boundary_type
        result["wait"] = wait
        return result

    async def compile(
        self, payload: WorkflowParticipantCompileRequest
    ) -> WorkflowParticipantResult:
        self._assert_runtime_type(payload.get("runtime_type"))
        return {
            "status": "compiled",
            "runtime_type": self.runtime_type,
            "deployment_id": str(payload["deployment_id"]),
            "graph_source": "customer_compiled",
        }

    async def _invoke(
        self,
        *,
        payload: WorkflowParticipantExecutionRequest,
        operation: str,
        graph_input: Any,
    ) -> WorkflowParticipantResult:
        execution_id = str(payload["execution_id"])
        if execution_id in self._cancelled_execution_ids:
            return self._result(status="cancelled", payload=payload)
        task = asyncio.current_task()
        if task is not None:
            self._active_tasks[execution_id] = task
        try:
            output = await self.graph.ainvoke(graph_input, config=self._config(payload))
            if execution_id in self._cancelled_execution_ids:
                return self._result(status="cancelled", payload=payload)
            if isinstance(output, dict) and output.get("__interrupt__"):
                return await self._suspend_for_interrupt(payload=payload, output=output)
            return self._result(status="completed", payload=payload, output=output)
        except asyncio.CancelledError:
            self._cancelled_execution_ids.add(execution_id)
            return self._result(status="cancelled", payload=payload)
        finally:
            if self._active_tasks.get(execution_id) is task:
                self._active_tasks.pop(execution_id, None)

    async def execute(
        self, payload: WorkflowParticipantExecutionRequest
    ) -> WorkflowParticipantResult:
        return await self._invoke(
            payload=payload,
            operation="execute",
            graph_input=_mapping(payload.get("runtime_input")),
        )

    async def resume(
        self, payload: WorkflowParticipantResumeRequest
    ) -> WorkflowParticipantResult:
        continuation = _mapping(payload.get("continuation"))
        wake_input = _mapping(payload.get("wake_input"))
        resume_value = continuation.get("resume", wake_input)
        update = _mapping(continuation.get("update")) or None
        return await self._invoke(
            payload=payload,
            operation="resume",
            graph_input=_langgraph_command(resume=resume_value, update=update),
        )

    async def cancel(
        self, payload: WorkflowParticipantCancelRequest
    ) -> WorkflowParticipantResult:
        execution_id = str(payload["execution_id"])
        self._cancelled_execution_ids.add(execution_id)
        task = self._active_tasks.get(execution_id)
        if task is not None and not task.done():
            task.cancel()
            status = "cancellation_requested"
        else:
            status = "cancelled"
        return {
            "status": status,
            "runtime_type": self.runtime_type,
            "execution_id": execution_id,
            "attempt_id": str(payload["attempt_id"]),
            "reason": str(payload.get("reason") or "cancelled"),
        }


__all__ = ["LangGraphWorkflowParticipant"]
