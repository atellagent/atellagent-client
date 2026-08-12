# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bridge connected agent deliveries to an existing customer HTTP API."""

from __future__ import annotations

import asyncio
import os

import httpx

from atellagent_client.examples import bundled_example_path
from atellagent_client.connected import ConnectedBridge, mount_agent_handler
from atellagent_client.sdk.config import load_service_account_config_from_yaml


TARGET_URL = os.getenv("TARGET_URL", "http://127.0.0.1:9000/v1/agent/turn")


async def adapter_handler(payload: dict) -> dict:
    """Propagate the stable delivery key to an idempotent customer target."""
    communication = payload.get("communication_metadata", {})
    effect_key = str(communication.get("communication_id") or "")
    if not effect_key:
        raise ValueError("connected agent delivery has no idempotency key")
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        response = await client.post(
            TARGET_URL,
            headers={"Idempotency-Key": effect_key},
            json=payload,
        )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("agent target returned non-object JSON")
    return result


async def run() -> None:
    config_path = os.getenv("CONFIG_PATH") or bundled_example_path(
        "config/agent_bridge.yaml"
    )
    config = load_service_account_config_from_yaml(config_path)
    runtime = ConnectedBridge(config)
    mount_agent_handler(runtime, adapter_handler, target_idempotent=True)
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(run())
