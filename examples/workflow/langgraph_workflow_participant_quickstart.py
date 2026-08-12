# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Run a customer-compiled LangGraph over one connected participant."""

from __future__ import annotations

import asyncio
import os
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from atellagent_client.examples import bundled_example_path
from atellagent_client.connected import mount_workflow_handler
from atellagent_client.integrations.workflows import LangGraphWorkflowParticipant
from atellagent_client.sdk import ConnectedSDKRuntime
from atellagent_client.sdk.config import load_service_account_config_from_yaml


class WorkflowState(TypedDict):
    message: str
    result: str


def respond(state: WorkflowState) -> dict:
    return {"result": f"Processed: {state['message']}"}


async def run() -> None:
    builder = StateGraph(WorkflowState)
    builder.add_node("respond", respond)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config_path = os.getenv("CONFIG_PATH") or bundled_example_path("config/workflow.yaml")
    runtime = ConnectedSDKRuntime(load_service_account_config_from_yaml(config_path))
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
    asyncio.run(run())
