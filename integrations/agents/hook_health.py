# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in
# the root directory of this source tree.

"""Privacy-bounded event receipts for supported local host hooks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


_HOST_EVENT_BY_METHOD = {
    "codex": {
        "model.decision": "UserPromptSubmit",
        "action.preflight": "PreToolUse",
        "action.postflight": "PostToolUse",
    },
}
_OUTCOMES = frozenset({"completed", "failed", "timed_out"})


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
        event_name = _HOST_EVENT_BY_METHOD.get(host, {}).get(method)
        if event_name is None or outcome not in _OUTCOMES:
            return
        if isinstance(elapsed_ms, bool) or not isinstance(elapsed_ms, int):
            return
        self._receipts[event_name] = {
            "event_name": event_name,
            "outcome": outcome,
            "elapsed_ms": min(8_000, max(0, elapsed_ms)),
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    def snapshot(self) -> list[dict[str, object]]:
        """Return a detached, content-free receipt set for the next heartbeat."""

        return [dict(self._receipts[event]) for event in sorted(self._receipts)]


__all__ = ["HookEventHealth"]
