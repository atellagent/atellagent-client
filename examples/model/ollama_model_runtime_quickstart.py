# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Run a customer-operated Ollama model over the connected protocol."""

from __future__ import annotations

import asyncio
import os

from atellagent_client.examples import bundled_example_path
from atellagent_client.connected import mount_model_handler
from atellagent_client.integrations.models import OllamaModelRuntimeHandler
from atellagent_client.sdk import ConnectedSDKRuntime
from atellagent_client.sdk.config import load_service_account_config_from_yaml


async def run() -> None:
    config_path = os.getenv("CONFIG_PATH") or bundled_example_path("config/model.yaml")
    handler = OllamaModelRuntimeHandler(host=os.getenv("OLLAMA_HOST"))
    runtime = ConnectedSDKRuntime(load_service_account_config_from_yaml(config_path))
    mount_model_handler(runtime, handler, target_idempotent=True)
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(run())
