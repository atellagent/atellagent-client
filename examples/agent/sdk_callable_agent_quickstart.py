# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Run a pure callable agent over the outbound connected-runtime protocol."""

from __future__ import annotations

import asyncio

from atellagent_client.examples.workload import load_workload_config, options
from atellagent_client.connected import mount_agent_handler
from atellagent_client.sdk import ConnectedSDKRuntime


def handler(payload: dict) -> dict:
    """A retry-safe, side-effect-free example handler."""
    messages = payload.get("state", {}).get("messages", [])
    last = messages[-1].get("content", "") if messages else ""
    return {"content": f"Echo: {last}", "metadata": {"example": True}}


async def run(directory, request_id) -> None:
    config = await load_workload_config(directory, request_id, integration_type="agent", packaging="sdk")
    runtime = ConnectedSDKRuntime(config)
    mount_agent_handler(
        runtime,
        handler,
        consequential=False,
        target_idempotent=False,
    )
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()


if __name__ == "__main__":
    args = options()
    asyncio.run(run(args.state_directory, args.request_id))
