# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Run a customer-compiled LangGraph over one connected participant."""

from __future__ import annotations

import asyncio
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from atellagent_client.examples.workload import load_workload_config, options
from atellagent_client.connected import mount_workflow_handler
from atellagent_client.integrations.workflows import LangGraphWorkflowParticipant
from atellagent_client.sdk import ConnectedSDKRuntime


class WorkflowState(TypedDict):
    message: str
    result: str


def respond(state: WorkflowState) -> dict:
    return {"result": f"Processed: {state['message']}"}


async def run(directory, request_id) -> None:
    config = await load_workload_config(directory, request_id, integration_type="workflow_runtime", packaging="sdk")
    builder = StateGraph(WorkflowState)
    builder.add_node("respond", respond)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    runtime = ConnectedSDKRuntime(config)
    mount_workflow_handler(
        runtime,
        LangGraphWorkflowParticipant(graph=graph),
        target_idempotent=True,
    )
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()


if __name__ == "__main__":
    args = options()
    asyncio.run(run(args.state_directory, args.request_id))
