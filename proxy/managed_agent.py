"""Machine-authorized direct SDK proxy from an approved saved deployment."""

import argparse
import asyncio
from pathlib import Path
import sys
from uuid import UUID

from atellagent_client.sdk import OnboardingClientError, WorkloadContext, create_service_account_client
from .agent import MCPAgentProxy
from .agent_tools import load_agent_tools
from .contracts import _ConfiguredMCPToolGateway


async def run(args, *, input_stream=None, output_stream=None):
    tools = load_agent_tools(args.tools)
    context = WorkloadContext(args.state_directory)
    flow = context.flow(args.request_id)
    proposal = flow.draft.proposal
    if proposal.account_type != "agent" or proposal.packaging != "sdk":
        raise ValueError("direct SDK MCP proxy requires an approved agent SDK profile")
    inputs = context.local_inputs.load(flow.draft)
    client = await create_service_account_client(flow=flow, deployment=inputs.deployment,
        local_guardrail_manifest_path=inputs.local_guardrail_manifest_path, timeout=30)
    try:
        proxy = MCPAgentProxy(gateway=_ConfiguredMCPToolGateway(client=client, tools=tools))
        for line in sys.stdin if input_stream is None else input_stream:
            if not line.strip():
                continue
            response = await proxy.handle_json_line(line)
            if response is not None:
                print(response, file=sys.stdout if output_stream is None else output_stream, flush=True)
    finally:
        await client.close_async()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run an approved machine-authorized MCP proxy over stdio.")
    parser.add_argument("--state-directory", required=True, type=Path)
    parser.add_argument("--request-id", required=True, type=UUID)
    parser.add_argument("--tools", required=True, type=Path, help="Local tool-only YAML catalog")
    args = parser.parse_args(argv)
    if not args.state_directory.is_absolute():
        parser.error("--state-directory must be absolute")
    try:
        return asyncio.run(run(args))
    except (ValueError, OSError, OnboardingClientError) as exc:
        message = exc.code if isinstance(exc, OnboardingClientError) else "invalid local proxy configuration"
        print(f"[ERROR] {message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
