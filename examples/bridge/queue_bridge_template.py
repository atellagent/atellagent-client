# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Template for an outbound connected participant backed by a durable queue."""

from __future__ import annotations

import argparse
import asyncio

from atellagent_client.connected import ConnectedBridge, mount_agent_handler
from atellagent_client.sdk.config import load_service_account_config_from_yaml


async def run(config_path: str) -> None:
    async def handler(payload: dict) -> dict:
        effect_key = str(
            payload.get("communication_metadata", {}).get("communication_id") or ""
        )
        # Publish with effect_key as the broker message/correlation identifier.
        # The consumer must durably deduplicate it before causing side effects.
        await asyncio.sleep(0)
        return {"content": f"replace with queue response for {effect_key}"}

    runtime = ConnectedBridge(load_service_account_config_from_yaml(config_path))
    mount_agent_handler(runtime, handler, target_idempotent=True)
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
