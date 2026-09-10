# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Owner-private correlation and delivery store for native hook outcomes."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Any, Mapping


_MAX_ENTRIES = 256
_MAX_FILE_BYTES = 1024 * 1024
_CORRELATION_TTL_SECONDS = 15 * 60


class HookOutcomeCorrelationUnavailable(ValueError):
    """No awaiting correlation exists for this host outcome."""


class HookOutcomeOutbox:
    """Persist opaque correlation and received safe outcome evidence only.

    Gateway remains the action lifecycle authority. An awaiting entry contains
    just the action key's opaque binding digest so a postflight survives a
    proxy restart; it expires independently when a host never supplies an
    outcome. Only received outcomes are eligible for delivery retries.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def undelivered_outcome_count(self) -> int:
        return sum(1 for entry in self._entries.values() if entry.get("state") == "received")

    async def load(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._load_sync)
            if await asyncio.to_thread(self._prune_sync):
                await asyncio.to_thread(self._write_sync)

    async def reserve(self, action_key: str, binding_fingerprint: str) -> None:
        if not action_key or len(binding_fingerprint) != 64:
            raise ValueError("hook outcome correlation is invalid")
        async with self._lock:
            if action_key in self._entries:
                raise ValueError("hook outcome correlation already exists")
            if len(self._entries) >= _MAX_ENTRIES:
                raise ValueError("hook outcome outbox is full")
            self._entries[action_key] = {
                "state": "awaiting_host",
                "binding_fingerprint": binding_fingerprint,
                "created_at": int(time.time()),
            }
            await asyncio.to_thread(self._write_sync)

    async def record_outcome(self, action_key: str, outcome: Mapping[str, Any]) -> None:
        async with self._lock:
            entry = self._entries.get(action_key)
            if not isinstance(entry, dict) or entry.get("state") != "awaiting_host":
                raise HookOutcomeCorrelationUnavailable("hook outcome correlation is unavailable")
            entry["state"] = "received"
            entry["outcome"] = dict(outcome)
            entry["received_at"] = int(time.time())
            await asyncio.to_thread(self._write_sync)

    async def received_entries(self) -> tuple[tuple[str, dict[str, Any]], ...]:
        async with self._lock:
            return tuple(
                (key, dict(entry))
                for key, entry in self._entries.items()
                if entry.get("state") == "received"
            )

    async def acknowledge(self, action_key: str) -> None:
        async with self._lock:
            if action_key in self._entries:
                self._entries.pop(action_key, None)
                await asyncio.to_thread(self._write_sync)

    async def prune(self) -> None:
        async with self._lock:
            changed = await asyncio.to_thread(self._prune_sync)
            if changed:
                await asyncio.to_thread(self._write_sync)

    def _load_sync(self) -> None:
        if not self.path.exists():
            self._entries = {}
            return
        metadata = self.path.stat()
        if (
            metadata.st_uid != os.getuid()
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size > _MAX_FILE_BYTES
        ):
            raise ValueError("hook outcome outbox is unsafe")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("hook outcome outbox is invalid") from exc
        raw_entries = payload.get("entries") if isinstance(payload, dict) else None
        if payload.get("version") != 1 or not isinstance(raw_entries, dict):
            raise ValueError("hook outcome outbox is invalid")
        if len(raw_entries) > _MAX_ENTRIES:
            raise ValueError("hook outcome outbox is invalid")
        self._entries = {
            key: dict(entry)
            for key, entry in raw_entries.items()
            if isinstance(key, str) and isinstance(entry, dict)
        }
        if len(self._entries) != len(raw_entries):
            raise ValueError("hook outcome outbox is invalid")

    def _prune_sync(self) -> bool:
        now = int(time.time())
        stale = [
            key
            for key, entry in self._entries.items()
            if entry.get("state") == "awaiting_host"
            and int(entry.get("created_at") or 0) + _CORRELATION_TTL_SECONDS <= now
        ]
        for key in stale:
            self._entries.pop(key, None)
        return bool(stale)

    def _write_sync(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        serialized = json.dumps(
            {"version": 1, "entries": self._entries},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(serialized) > _MAX_FILE_BYTES:
            raise ValueError("hook outcome outbox exceeds size limit")
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)


__all__ = ["HookOutcomeOutbox"]
