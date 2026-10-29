# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bounded credential-free local hook transport and endpoint validation."""

import asyncio
import os
from pathlib import Path
from typing import Any, Mapping
from .hook_control import HookControlClient
from .host_hook_contracts import ADAPTER_DEADLINE_SECONDS
from .windows_named_pipe import validate_windows_named_pipe


def _is_windows() -> bool:
    return os.name == "nt"


async def _call(
    socket_path: str, method: str, params: Mapping[str, Any]
) -> dict[str, Any]:
    client = HookControlClient(socket_path, timeout_seconds=ADAPTER_DEADLINE_SECONDS)
    return await asyncio.wait_for(
        client.call(method, params), timeout=ADAPTER_DEADLINE_SECONDS
    )


def _valid_control_endpoint(endpoint: str) -> bool:
    if _is_windows():
        try:
            validate_windows_named_pipe(endpoint)
        except ValueError:
            return False
        return True
    return Path(endpoint).is_absolute()
