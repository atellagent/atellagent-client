# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Run Slack egress over one outbound connected participant."""

from __future__ import annotations

import asyncio
import os

from atellagent_client.examples import bundled_example_path
from atellagent_client.integrations.channels import ChannelAdapterRegistry, SlackChannelAdapter
from atellagent_client.connected import ConnectedBridge, mount_channel_registry
from atellagent_client.sdk.config import load_service_account_config_from_yaml


async def run() -> None:
    config_path = os.getenv("CONFIG_PATH") or bundled_example_path("config/channel.yaml")
    registry = ChannelAdapterRegistry()
    registry.register(
        SlackChannelAdapter(
            bot_token=os.environ["SLACK_BOT_TOKEN"],
            verify_signatures=False,
        )
    )
    runtime = ConnectedBridge(load_service_account_config_from_yaml(config_path))
    mount_channel_registry(runtime, registry, target_idempotent=True)
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(run())
