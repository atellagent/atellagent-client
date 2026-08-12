# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Template for an outbound connected participant backed by a gRPC target."""

from __future__ import annotations

import argparse
import asyncio

import grpc

from atellagent_client.connected import ConnectedBridge, mount_agent_handler
from atellagent_client.sdk.config import load_service_account_config_from_yaml


async def run(config_path: str, grpc_target: str) -> None:
    channel = grpc.aio.insecure_channel(grpc_target)

    async def handler(payload: dict) -> dict:
        effect_key = str(
            payload.get("communication_metadata", {}).get("communication_id") or ""
        )
        # Replace this with a generated stub call and propagate effect_key in
        # gRPC metadata. The target must durably deduplicate that value.
        return {"content": f"replace with gRPC result for {effect_key}"}

    runtime = ConnectedBridge(load_service_account_config_from_yaml(config_path))
    mount_agent_handler(runtime, handler, target_idempotent=True)
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()
        await channel.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--target", required=True, help="For example: 127.0.0.1:50051")
    args = parser.parse_args()
    asyncio.run(run(args.config, args.target))


if __name__ == "__main__":
    main()
