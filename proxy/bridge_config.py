# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Configuration shared by the local MCP bridge and control runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


@dataclass(frozen=True)
class LocalMCPBridgeConfig:
    """Socket-only configuration for one owner-private local MCP bridge."""

    control_socket: str


def load_local_mcp_bridge_config(path: str) -> LocalMCPBridgeConfig:
    """Load the local-proxy-only bridge format without any credentials."""

    document = _object(
        yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {},
        "MCP bridge configuration",
    )
    if set(document) != {"control_socket"}:
        raise ValueError("MCP bridge configuration must contain only control_socket")
    control_socket = str(document.get("control_socket") or "").strip()
    if not control_socket or not Path(control_socket).is_absolute():
        raise ValueError("control_socket must be an absolute path")
    return LocalMCPBridgeConfig(control_socket=control_socket)


__all__ = ["LocalMCPBridgeConfig", "load_local_mcp_bridge_config"]
