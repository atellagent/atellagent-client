# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Exact Codex terminal goal-transition recovery rules."""

from typing import Any, Mapping
from .hook_control import HookControlError


def _is_codex_goal_lifecycle_transition(event: Mapping[str, Any]) -> bool:
    """Recognize exact host-owned goal transitions eligible for recovery."""
    arguments = event.get("tool_input")
    return (
        event.get("hook_event_name") in {"PreToolUse", "PostToolUse"}
        and event.get("tool_name") == "update_goal"
        and isinstance(arguments, Mapping)
        and dict(arguments)
        in (
            {"status": "blocked"},
            {"status": "paused"},
            {"status": "complete"},
        )
    )


def _goal_lifecycle_recovery_allowed(exc: BaseException) -> bool:
    """Preserve policy refusals and malformed inputs; recover evaluation failures."""
    if isinstance(exc, HookControlError):
        # A controller started before the adapter was updated can reject a new
        # adapter-owned field. Exact terminal goal transitions must still end.
        if exc.code in {"unsupported_params_field", "unsupported_obligation"}:
            return True
        return exc.code not in {
            "hook_input_invalid",
            "policy_denied",
        } and not exc.code.startswith(("invalid_", "unsupported_"))
    return True
