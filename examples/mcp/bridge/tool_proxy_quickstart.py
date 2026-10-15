# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Run an enrolled Tool Proxy against a customer-local MCP 2026-07-28 target."""

from __future__ import annotations

import asyncio

from atellagent_client.examples.workload import load_workload_config, options
from atellagent_client.connected import (
    ConnectedBridge,
    LocalMCPClient,
    mount_mcp_handler,
)
from atellagent_client.sdk.config import (
    BridgeDeploymentConfig,
)


async def run(directory, request_id) -> None:
    config = await load_workload_config(directory, request_id, integration_type="mcp", packaging="bridge")
    if not isinstance(config.deployment, BridgeDeploymentConfig):
        raise ValueError("Tool Proxy example requires bridge packaging")
    target = LocalMCPClient(config.deployment)
    runtime = None
    try:
        runtime = ConnectedBridge(config, mcp_manifest=await target.manifest())
        mount_mcp_handler(runtime, target.invoke, consequential=True, target_idempotent=True)
        await runtime.run_forever()
    finally:
        try:
            if runtime is not None: await runtime.stop()
        finally:
            await target.close()


if __name__ == "__main__":
    args = options()
    asyncio.run(run(args.state_directory, args.request_id))
