# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Capability metadata for public callable-agent integrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

from atellagent_client.protocol.runtime_modes import (
    normalize_runtime_mode,
    runtime_mode_auto_monitors_wait_boundary,
    supported_wait_boundary_types_for_runtime_mode,
)


@dataclass(frozen=True)
class ProviderCapabilitySet:
    provider: str
    sdk_nouns: Tuple[str, ...]
    tool_boundary_only: Tuple[str, ...] = field(default_factory=tuple)
    model_checkpoint_aware: Tuple[str, ...] = field(default_factory=tuple)
    session_state_aware: Tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "sdk_nouns": list(self.sdk_nouns),
            "tool_boundary_only": list(self.tool_boundary_only),
            "model_checkpoint_aware": list(self.model_checkpoint_aware),
            "session_state_aware": list(self.session_state_aware),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class RuntimeWaitCapabilitySet:
    runtime_mode: str
    supported_wait_boundary_types: Tuple[str, ...]
    auto_monitored_wait_boundary_types: Tuple[str, ...]
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "runtime_mode": self.runtime_mode,
            "supported_wait_boundary_types": list(self.supported_wait_boundary_types),
            "auto_monitored_wait_boundary_types": list(
                self.auto_monitored_wait_boundary_types
            ),
            "notes": self.notes,
        }


def runtime_wait_capability_set(runtime_mode: str) -> RuntimeWaitCapabilitySet:
    normalized_mode = normalize_runtime_mode(runtime_mode)
    if normalized_mode is None:
        raise ValueError("runtime_mode must be one of 'bridge' or 'sdk'")
    supported_waits = tuple(supported_wait_boundary_types_for_runtime_mode(normalized_mode))
    auto_monitored = tuple(
        wait_boundary_type
        for wait_boundary_type in supported_waits
        if runtime_mode_auto_monitors_wait_boundary(normalized_mode, wait_boundary_type)
    )
    notes = (
        "Bridge participation supports governed execution boundaries but fails closed "
        "for durable waits."
        if normalized_mode == "bridge"
        else "Embedded SDK participation supports governed calls but fails closed "
        "for durable waits."
    )
    return RuntimeWaitCapabilitySet(
        runtime_mode=normalized_mode,
        supported_wait_boundary_types=supported_waits,
        auto_monitored_wait_boundary_types=auto_monitored,
        notes=notes,
    )


__all__ = [
    "ProviderCapabilitySet",
    "RuntimeWaitCapabilitySet",
    "runtime_wait_capability_set",
]
