# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public host-hook coverage and response contracts."""

from dataclasses import dataclass
from typing import Any

ADAPTER_DEADLINE_SECONDS = 19.0
"""Bounded local-control deadline; host templates use a 20-second timeout."""

_HOST_NAMES = {"claude-code", "codex", "gemini-cli"}
HOST_HOOK_ADAPTER_VERSION = "atellagent.host-hooks.v1"


@dataclass(frozen=True)
class HookAdapterResponse:
    """A complete host command-hook response."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


def host_hook_capabilities() -> dict[str, Any]:
    """Return the public machine-readable coverage declaration."""
    return {
        "schema_version": "atellagent.host-hooks.v1",
        "adapter_deadline_seconds": ADAPTER_DEADLINE_SECONDS,
        "hosts": {
            "claude-code": {
                "transport": "command",
                "events": {
                    "UserPromptSubmit": "turn_entry",
                    "PreToolUse": "preflight",
                    "PostToolUse": "postflight_success",
                    "PostToolUseFailure": "postflight_failure",
                },
                "exclusions": [],
            },
            "codex": {
                "transport": "command",
                "events": {
                    "UserPromptSubmit": "turn_entry",
                    "PreToolUse": "preflight",
                    "PostToolUse": "postflight_result",
                },
                "exclusions": [
                    "hosted or specialized tool paths outside documented command hooks",
                ],
            },
            "gemini-cli": {
                "transport": "command",
                "events": {
                    "BeforeModel": "full_model_request",
                    "BeforeTool": "preflight",
                },
                "exclusions": [
                    "AfterModel (not supported)",
                    "tool outcomes without a stable host tool-call identifier",
                ],
            },
        },
    }
