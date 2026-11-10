# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Run Slack egress over one outbound connected participant."""

from __future__ import annotations

import asyncio
import os

from atellagent_client.examples.workload import load_workload_config, options
from atellagent_client.integrations.channels import ChannelAdapterRegistry, SlackChannelAdapter
from atellagent_client.connected import ConnectedBridge, mount_channel_registry


async def run(directory, request_id) -> None:
    config = await load_workload_config(directory, request_id, integration_type="channel", packaging="bridge")
    registry = ChannelAdapterRegistry()
    registry.register(
        SlackChannelAdapter(
            bot_token=os.environ["SLACK_BOT_TOKEN"],
            verify_signatures=False,
        )
    )
    runtime = ConnectedBridge(config)
    mount_channel_registry(runtime, registry, target_idempotent=True)
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()


if __name__ == "__main__":
    args = options()
    asyncio.run(run(args.state_directory, args.request_id))
