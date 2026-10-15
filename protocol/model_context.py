# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under LICENSE.md in this source tree.

"""Request-bound accounting on the governed connected model transport.

Measurement is never generation authorization. Limits and counts must come
from the selected model's real tokenizer; consumers recheck policy/liveness.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

SCHEMA = "atellagent.connected.model-context.v1"


def model_context_input(request) -> dict:
    """Exactly the generation input exposed to a ModelRuntimeHandler.

    Routing, request IDs, continuity purpose and workflow identity are not model
    tokens. Every generation option and complete message/tool document is kept.
    """
    result = {
        "model": request.model,
        "provider": request.provider,
        "messages": request.messages,
        "stream": request.stream,
        **request.options,
    }
    encoded = json.dumps(
        result,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    if len(encoded) > 2**20:
        raise ValueError("Bounded model context input required")
    return json.loads(encoded)


def model_context_digest(request) -> str:
    raw = json.dumps(
        model_context_input(request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def validate_model_context_result(value: Any, request) -> dict:
    fields = {
        "schema",
        "model",
        "request_digest",
        "input_tokens",
        "estimated",
        "counter_version",
        "limits",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
        or value["schema"] != SCHEMA
        or value["model"] != request.model
        or value["request_digest"] != model_context_digest(request)
        or type(value["input_tokens"]) is not int
        or not 0 <= value["input_tokens"] <= 2**53 - 1
        or type(value["estimated"]) is not bool
        or not isinstance(value["counter_version"], str)
        or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value["counter_version"]
        )
    ):
        raise ValueError("Invalid request-bound model context measurement")
    limits = value["limits"]
    if (
        not isinstance(limits, Mapping)
        or not set(limits)
        <= {"context_window_tokens", "max_input_tokens", "max_output_tokens"}
        or "max_output_tokens" not in limits
        or not {"context_window_tokens", "max_input_tokens"} & set(limits)
        or any(type(n) is not int or not 1 <= n <= 2**53 - 1 for n in limits.values())
    ):
        raise ValueError("Invalid model context limits")
    return {**dict(value), "limits": dict(limits)}
