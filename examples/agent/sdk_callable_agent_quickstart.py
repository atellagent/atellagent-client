# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Run a pure callable agent over the outbound connected-runtime protocol."""

from __future__ import annotations

import asyncio
import os

from atellagent_client.examples import bundled_example_path
from atellagent_client.connected import mount_agent_handler
from atellagent_client.sdk import ConnectedSDKRuntime
from atellagent_client.sdk.config import load_service_account_config_from_yaml


def handler(payload: dict) -> dict:
    """A retry-safe, side-effect-free example handler."""
    messages = payload.get("state", {}).get("messages", [])
    last = messages[-1].get("content", "") if messages else ""
    return {"content": f"Echo: {last}", "metadata": {"example": True}}


async def run() -> None:
    config_path = os.getenv("CONFIG_PATH") or bundled_example_path("config/agent.yaml")
    config = load_service_account_config_from_yaml(config_path)
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
    asyncio.run(run())
