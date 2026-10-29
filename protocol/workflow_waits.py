# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public workflow wait taxonomy helpers owned by the protocol."""

from __future__ import annotations

from typing import Any, List, Optional

from .runtime_modes import (
    normalize_runtime_mode,
    supported_wait_boundary_types_for_runtime_mode,
)


_SUPPORTED_WORKFLOW_WAIT_BOUNDARY_TYPES = (
    "async_tool_completion",
    "human_approval",
    "timer_backoff",
    "webhook_event",
)
_APPROVAL_REQUIRED_WORKFLOW_WAITS = frozenset({"human_approval"})
_EXECUTION_DISPATCH_REQUIRED_WORKFLOW_WAITS = frozenset({"webhook_event"})
_WAIT_REQUIRED_WORKFLOW_WAITS = frozenset(_SUPPORTED_WORKFLOW_WAIT_BOUNDARY_TYPES)
_SESSION_REQUIRED_WORKFLOW_WAITS = frozenset()


def _normalize_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def normalize_workflow_wait_boundary_type(wait_boundary_type: Any) -> Optional[str]:
    normalized = _normalize_text(wait_boundary_type)
    if normalized is None:
        return None
    normalized = normalized.lower()
    if normalized not in _SUPPORTED_WORKFLOW_WAIT_BOUNDARY_TYPES:
        return None
    return normalized


def supported_public_workflow_wait_boundary_types() -> List[str]:
    return list(_SUPPORTED_WORKFLOW_WAIT_BOUNDARY_TYPES)


def default_public_workflow_wait_boundary_types_for_runtime_mode(
    runtime_mode: Any,
) -> List[str]:
    normalized_mode = normalize_runtime_mode(runtime_mode)
    if not normalized_mode:
        return []
    return [
        wait_boundary_type
        for wait_boundary_type in supported_wait_boundary_types_for_runtime_mode(
            normalized_mode
        )
        if normalize_workflow_wait_boundary_type(wait_boundary_type)
    ]


def workflow_wait_boundary_requires_approvals(wait_boundary_type: Any) -> bool:
    normalized = normalize_workflow_wait_boundary_type(wait_boundary_type)
    return bool(normalized and normalized in _APPROVAL_REQUIRED_WORKFLOW_WAITS)


def workflow_wait_boundary_requires_execution_dispatch(wait_boundary_type: Any) -> bool:
    normalized = normalize_workflow_wait_boundary_type(wait_boundary_type)
    return bool(
        normalized and normalized in _EXECUTION_DISPATCH_REQUIRED_WORKFLOW_WAITS
    )


def workflow_wait_boundary_requires_waits(wait_boundary_type: Any) -> bool:
    normalized = normalize_workflow_wait_boundary_type(wait_boundary_type)
    return bool(normalized and normalized in _WAIT_REQUIRED_WORKFLOW_WAITS)


def workflow_wait_boundary_requires_sessions(wait_boundary_type: Any) -> bool:
    normalized = normalize_workflow_wait_boundary_type(wait_boundary_type)
    return bool(normalized and normalized in _SESSION_REQUIRED_WORKFLOW_WAITS)


__all__ = [
    "default_public_workflow_wait_boundary_types_for_runtime_mode",
    "normalize_workflow_wait_boundary_type",
    "supported_public_workflow_wait_boundary_types",
    "workflow_wait_boundary_requires_approvals",
    "workflow_wait_boundary_requires_waits",
    "workflow_wait_boundary_requires_execution_dispatch",
    "workflow_wait_boundary_requires_sessions",
]
