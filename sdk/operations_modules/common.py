# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Shared helpers for SDK API operation handlers."""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

ALLOWED_MODEL_INVOCATION_FIELDS = {
    "memory_thread_id",
    "model",
    "provider",
    "response_mode",
    "max_output_tokens",
    "reasoning",
    "verbosity",
    "tool_mode",
    "tool_definitions",
    "tool_choice",
    "parallel_tool_calls",
    "structured_output",
    "stop_sequences",
    "user",
    "sampling",
    "seed",
    "metadata",
}


def sanitize_model_invocation_options(options: Dict[str, Any]) -> Dict[str, Any]:
    if not options:
        return {}
    sanitized: Dict[str, Any] = {}
    for key, value in options.items():
        if key not in ALLOWED_MODEL_INVOCATION_FIELDS:
            logger.debug("Ignoring unsupported model invocation option '%s'", key)
            continue
        if value is not None:
            sanitized[key] = value
    return sanitized


def extract_policy_detail(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return detail
    if isinstance(payload.get("error"), dict):
        return payload["error"]
    return payload
