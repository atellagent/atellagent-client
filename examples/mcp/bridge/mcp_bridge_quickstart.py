# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bridge deliveries to a local stateless MCP 2026-07-28 target."""

from __future__ import annotations

import asyncio
import os

from atellagent_client.examples import bundled_example_path
from atellagent_client.connected import (
    ConnectedBridge,
    LocalMCPClient,
    mount_mcp_handler,
)
from atellagent_client.sdk.config import (
    BridgeDeploymentConfig,
    load_service_account_config_from_yaml,
)


async def run() -> None:
    config_path = os.getenv("CONFIG_PATH") or bundled_example_path("config/mcp.yaml")
    config = load_service_account_config_from_yaml(config_path)
    if not isinstance(config.deployment, BridgeDeploymentConfig):
        raise ValueError("MCP bridge example requires bridge packaging")
    target = LocalMCPClient(config.deployment)
    runtime = ConnectedBridge(config, mcp_manifest=await target.manifest())
    mount_mcp_handler(
        runtime,
        target.invoke,
        consequential=True,
        target_idempotent=True,
    )
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()
        await target.close()


if __name__ == "__main__":
    asyncio.run(run())
