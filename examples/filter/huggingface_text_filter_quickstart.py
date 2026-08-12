# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Run a Hugging Face classifier over the outbound connected protocol."""

from __future__ import annotations

import asyncio
import os

from atellagent_client.examples import bundled_example_path
from atellagent_client.connected import mount_filter_handler
from atellagent_client.integrations.models import HuggingFaceTextClassificationFilter
from atellagent_client.sdk import ConnectedSDKRuntime
from atellagent_client.sdk.config import load_service_account_config_from_yaml


async def run() -> None:
    config_path = os.getenv("CONFIG_PATH") or bundled_example_path("config/filter.yaml")
    handler = HuggingFaceTextClassificationFilter(
        model_id=os.getenv("HF_FILTER_MODEL", "unitary/toxic-bert"),
        blocked_labels=("toxic",),
        threshold=float(os.getenv("HF_FILTER_THRESHOLD", "0.5")),
    )
    runtime = ConnectedSDKRuntime(load_service_account_config_from_yaml(config_path))
    mount_filter_handler(runtime, handler, target_idempotent=True)
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(run())
