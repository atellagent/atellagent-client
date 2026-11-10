# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Client-facing runtime mode capability helpers for the public protocol."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .agent_waits import normalize_wait_boundary_type

_RUNTIME_MODES: tuple[str, ...] = ("bridge", "sdk")

_RUNTIME_MODE_SUPPORTED_WAITS: Dict[str, tuple[str, ...]] = {
    "bridge": (),
    "sdk": (),
}

_RUNTIME_MODE_AUTOMATIC_WAIT_MONITORS: Dict[str, tuple[str, ...]] = {
    "bridge": (),
    "sdk": (),
}


def normalize_runtime_mode(runtime_mode: Any) -> Optional[str]:
    if runtime_mode is None:
        return None
    normalized = str(runtime_mode).strip().lower()
    if normalized in _RUNTIME_MODES:
        return normalized
    return None


def supported_runtime_modes() -> List[str]:
    return list(_RUNTIME_MODES)


def supported_wait_boundary_types_for_runtime_mode(runtime_mode: Any) -> List[str]:
    normalized_mode = normalize_runtime_mode(runtime_mode)
    if not normalized_mode:
        return []
    return list(_RUNTIME_MODE_SUPPORTED_WAITS.get(normalized_mode, ()))


def runtime_mode_supports_wait_boundary_type(
    runtime_mode: Any,
    wait_boundary_type: Any,
) -> bool:
    normalized_mode = normalize_runtime_mode(runtime_mode)
    normalized_boundary = normalize_wait_boundary_type(wait_boundary_type)
    if not normalized_mode or not normalized_boundary:
        return False
    return normalized_boundary in _RUNTIME_MODE_SUPPORTED_WAITS.get(
        normalized_mode,
        (),
    )


def runtime_mode_auto_monitors_wait_boundary(
    runtime_mode: Any,
    wait_boundary_type: Any,
) -> bool:
    normalized_mode = normalize_runtime_mode(runtime_mode)
    normalized_boundary = normalize_wait_boundary_type(wait_boundary_type)
    if not normalized_mode or not normalized_boundary:
        return False
    return normalized_boundary in _RUNTIME_MODE_AUTOMATIC_WAIT_MONITORS.get(
        normalized_mode,
        (),
    )


__all__ = [
    "normalize_runtime_mode",
    "runtime_mode_auto_monitors_wait_boundary",
    "runtime_mode_supports_wait_boundary_type",
    "supported_runtime_modes",
    "supported_wait_boundary_types_for_runtime_mode",
]
