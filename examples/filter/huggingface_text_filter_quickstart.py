# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Run a Hugging Face classifier over the outbound connected protocol."""

from __future__ import annotations

import asyncio
import os

from atellagent_client.examples.workload import load_workload_config, options
from atellagent_client.connected import mount_filter_handler
from atellagent_client.integrations.models import HuggingFaceTextClassificationFilter
from atellagent_client.sdk import ConnectedSDKRuntime


async def run(directory, request_id) -> None:
    config = await load_workload_config(directory, request_id, integration_type="ml_filter", packaging="sdk")
    handler = HuggingFaceTextClassificationFilter(
        model_id=os.environ["HF_FILTER_MODEL"],
        blocked_labels=(os.environ["HF_FILTER_BLOCKED_LABEL"],),
        threshold=float(os.getenv("HF_FILTER_THRESHOLD", "0.5")),
    )
    runtime = ConnectedSDKRuntime(config)
    mount_filter_handler(runtime, handler, target_idempotent=True)
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()


if __name__ == "__main__":
    args = options()
    asyncio.run(run(args.state_directory, args.request_id))
