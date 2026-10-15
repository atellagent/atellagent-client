# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Run one LangChain agent turn with routed models and an approved MCP tool."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from langchain.agents import create_agent
from langchain_core.tools import InjectedToolCallId, tool

from atellagent_client.sdk import WorkloadContext, create_service_account_client
from atellagent_client.examples.agent.langchain_model import ExampleChatModel


def make_echo_tool(client, target_binding: str, tool_name: str):
    @tool
    async def governed_echo(text: str, tool_call_id: Annotated[str, InjectedToolCallId]) -> str:
        """Echo text using the configured, policy-controlled MCP server."""
        result = await client.call_mcp_tool_result_async(
            target_binding, tool_name, {"text": text}, tool_call_id=tool_call_id,
            action_context={"action_key": f"langchain-{uuid4()}"},
            poll_timeout_seconds=30,
        )
        if result.get("isError"):
            raise RuntimeError("The MCP server reported a tool error")
        return json.dumps(result, allow_nan=False)
    return governed_echo


async def run(args):
    context = WorkloadContext(args.state_directory)
    proposal = context.proposal(args.request_id)
    if proposal.account_type != "agent" or proposal.packaging != "sdk":
        raise ValueError("This example requires an approved SDK agent workload")
    client = await create_service_account_client(flow=context.flow(args.request_id), timeout=30)
    async with client:
        model = ExampleChatModel(client=client, model_name=args.model, provider=args.provider,
                                 reasoning_effort=args.reasoning_effort)
        agent = create_agent(model, tools=[make_echo_tool(client, args.target_binding, args.tool_name)],
            system_prompt="Use governed_echo exactly once for a requested echo. Report its actual result. Do not invent tool results.")
        result = await agent.ainvoke({"messages": [{"role": "user", "content": args.prompt}]},
                                    config={"recursion_limit": 8})
        print(result["messages"][-1].content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-directory", required=True, type=Path)
    parser.add_argument("--request-id", required=True, type=UUID)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", choices=["none", "low", "medium", "high"])
    parser.add_argument("--target-binding", required=True)
    parser.add_argument("--tool-name", default="echo")
    parser.add_argument("--prompt", default="Echo the text hello using governed_echo, then summarize the result.")
    args = parser.parse_args()
    if not args.state_directory.is_absolute():
        parser.error("--state-directory must be absolute")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
