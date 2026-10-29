# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in
# the root directory of this source tree.

"""Privacy-bounded event receipts for supported local host hooks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


_HOST_EVENT_BY_METHOD = {
    "claude-code": {
        "model.decision": "UserPromptSubmit",
        "action.preflight": "PreToolUse",
        "action.postflight": ("PostToolUse", "PostToolUseFailure"),
    },
    "codex": {
        "model.decision": "UserPromptSubmit",
        "action.preflight": "PreToolUse",
        "action.postflight": "PostToolUse",
    },
}
_OUTCOMES = frozenset({"completed", "failed", "timed_out"})
_MAX_REPORT_AGE_SECONDS = 23 * 60 * 60
_MAX_FUTURE_SKEW_SECONDS = 30


class HookEventHealth:
    """Retain only the most recent local receipt for each configured event."""

    def __init__(self) -> None:
        self._receipts: dict[str, dict[str, object]] = {}

    def record(
        self,
        *,
        method: str,
        params: Any,
        outcome: str,
        elapsed_ms: int,
    ) -> None:
        if not isinstance(params, Mapping):
            return
        host = str(params.get("host") or "").strip().lower()
        expected_event = _HOST_EVENT_BY_METHOD.get(host, {}).get(method)
        if expected_event is None or outcome not in _OUTCOMES:
            return
        if isinstance(expected_event, tuple):
            event_name = str(params.get("hook_event_name") or "")
            if event_name not in expected_event:
                return
        else:
            event_name = expected_event
        if isinstance(elapsed_ms, bool) or not isinstance(elapsed_ms, int):
            return
        self._receipts[event_name] = {
            "event_name": event_name,
            "outcome": outcome,
            "elapsed_ms": min(30_000, max(0, elapsed_ms)),
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    def snapshot(self) -> list[dict[str, object]]:
        """Return only receipts the gateway can still accept on a heartbeat."""

        now = datetime.now(timezone.utc)
        fresh: list[dict[str, object]] = []
        for event in sorted(self._receipts):
            receipt = self._receipts[event]
            observed_at = receipt.get("observed_at")
            try:
                observed = datetime.fromisoformat(str(observed_at))
                age_seconds = (now - observed).total_seconds()
            except (TypeError, ValueError):
                age_seconds = float("inf")
            if -_MAX_FUTURE_SKEW_SECONDS <= age_seconds <= _MAX_REPORT_AGE_SECONDS:
                fresh.append(dict(receipt))
            else:
                del self._receipts[event]
        return fresh


__all__ = ["HookEventHealth"]
