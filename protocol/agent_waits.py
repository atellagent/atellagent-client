# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public versioned agent wait taxonomy helpers."""

from __future__ import annotations

from typing import Any, List, Optional

_SUPPORTED_WAIT_BOUNDARY_TYPES: tuple[str, ...] = (
    "model_wait",
    "async_tool_completion",
    "human_approval",
    "timer_backoff",
    "webhook_event",
)


def _normalize_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def normalize_wait_boundary_type(wait_boundary_type: Any) -> Optional[str]:
    normalized = _normalize_text(wait_boundary_type)
    if not normalized:
        return None
    normalized = normalized.lower()
    return normalized if normalized in _SUPPORTED_WAIT_BOUNDARY_TYPES else None


def supported_wait_boundary_types() -> List[str]:
    return list(_SUPPORTED_WAIT_BOUNDARY_TYPES)


__all__ = [
    "normalize_wait_boundary_type",
    "supported_wait_boundary_types",
]
