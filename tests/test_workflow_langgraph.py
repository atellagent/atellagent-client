# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Credential-free checks for the public LangGraph workflow participant."""

from __future__ import annotations

import asyncio
import unittest
from typing import TypedDict

from atellagent_client.integrations.workflows import LangGraphWorkflowParticipant
from atellagent_client.integrations.workflows.contracts import (
    coerce_workflow_participant_cancel_request,
    coerce_workflow_participant_compile_request,
    coerce_workflow_participant_execute_request,
    coerce_workflow_participant_resume_request,
)


def _execute_payload(*, execution_id: str = "execution-1", operation: str = "execute"):
    return {
        "protocol_version": "v1",
        "operation": operation,
        "execution_id": execution_id,
        "deployment_id": "deployment-1",
        "attempt_id": "attempt-1",
        "request_id": "request-1",
        "runtime_input": {"value": 1},
        "workflow_context": {"tenant_id": "tenant-1"},
    }


class LangGraphWorkflowParticipantTests(unittest.TestCase):
    def test_contract_coercers_produce_connected_workflow_payloads(self) -> None:
        compile_payload = coerce_workflow_participant_compile_request(
            {
                "protocol_version": "v1",
                "tenant_id": "tenant-1",
                "deployment_id": "deployment-1",
                "runtime_type": "langgraph",
                "workflow_definition": {},
                "runtime_configuration": {},
            }
        )
        execute_payload = coerce_workflow_participant_execute_request(
            {
                **_execute_payload(),
            }
        )
        resume_payload = coerce_workflow_participant_resume_request(
            {
                **_execute_payload(operation="resume"),
                "continuation": {},
                "wake_input": {},
            }
        )
        cancel_payload = coerce_workflow_participant_cancel_request(
            {
                "protocol_version": "v1",
                "operation": "cancel",
                "tenant_id": "tenant-1",
                "execution_id": "execution-1",
                "attempt_id": "cancel:execution-1",
                "reason": "cancelled",
            }
        )
        self.assertEqual(compile_payload["protocol_version"], "v1")
        self.assertEqual(execute_payload["execution_id"], "execution-1")
        self.assertEqual(resume_payload["operation"], "resume")
        self.assertEqual(cancel_payload["operation"], "cancel")

    def test_compiled_graph_executes_and_resumes_an_interrupt(self) -> None:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt

        class State(TypedDict):
            value: int
            approved: bool

        def review(state: State):
            approved = interrupt({"value": state["value"]})
            return {"approved": bool(approved)}

        builder = StateGraph(State)
        builder.add_node("review", review)
        builder.add_edge(START, "review")
        builder.add_edge("review", END)

        participant = LangGraphWorkflowParticipant(
            graph=builder.compile(checkpointer=InMemorySaver()),
        )

        async def run() -> None:
            compile_result = await participant.compile(
                {
                    "runtime_type": "langgraph",
                    "deployment_id": "deployment-1",
                    "workflow_definition": {},
                    "runtime_configuration": {},
                }
            )
            self.assertEqual(compile_result["graph_source"], "customer_compiled")

            first = await participant.execute(_execute_payload())
            self.assertEqual(first["status"], "suspended")
            self.assertEqual(first["wait_boundary_type"], "human_approval")
            self.assertEqual(
                first["wait"]["wait_id"],
                "langgraph:execution-1:attempt-1",
            )
            self.assertEqual(
                first["wait"]["idempotency_key"],
                "attempt-1:langgraph-interrupt",
            )

            resume_payload = _execute_payload(operation="resume")
            resume_payload["continuation"] = {"resume": True}
            resume_payload["wake_input"] = {}
            resumed = await participant.resume(resume_payload)
            self.assertEqual(resumed["output"]["approved"], True)

        asyncio.run(run())

    def test_cancellation_stops_an_active_graph_invocation(self) -> None:
        class Graph:
            def __init__(self) -> None:
                self.started = asyncio.Event()

            async def ainvoke(self, *_args, **_kwargs):
                self.started.set()
                await asyncio.Event().wait()

        graph = Graph()
        participant = LangGraphWorkflowParticipant(graph=graph)

        async def run() -> None:
            execution = asyncio.create_task(participant.execute(_execute_payload()))
            await graph.started.wait()
            cancelled = await participant.cancel(
                {
                    "execution_id": "execution-1",
                    "attempt_id": "attempt-1",
                    "reason": "user requested cancellation",
                }
            )
            self.assertEqual(cancelled["status"], "cancellation_requested")
            result = await execution
            self.assertEqual(result["status"], "cancelled")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
