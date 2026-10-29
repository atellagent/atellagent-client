# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Owner-private correlation and delivery store for native hook outcomes."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import hmac
import logging
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping


_MAX_AWAITING_HOST = 256
# Pending callbacks reserve delivery slots too, so recording an admitted
# action's outcome never competes with newer preflights for capacity.
_MAX_ENTRIES = 512
_MAX_FILE_BYTES = 1024 * 1024
_CORRELATION_TTL_SECONDS = 15 * 60
_OUTBOX_FORMAT_VERSION = 1
_LOGGER = logging.getLogger(__name__)


class HookOutcomeCorrelationUnavailable(ValueError):
    """No awaiting correlation exists for this host outcome."""


class HookOutcomeBindingMismatch(ValueError):
    """Observed host input differs from the reserved admission."""


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
        self._pending_storage: asyncio.Task[None] | None = None
        self._warned_backlogs: set[str] = set()

    @asynccontextmanager
    async def _serialized(self) -> AsyncIterator[None]:
        async with self._lock:
            if self._pending_storage is not None:
                # Cancelling a caller cannot stop a running disk thread. Wait
                # for that commit before deriving another state transition.
                # A failed prior write left the previous state intact, so a
                # subsequent caller may make a fresh transition normally.
                with suppress(Exception):
                    await asyncio.shield(self._pending_storage)
                self._pending_storage = None
            yield

    async def _storage(self, operation: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.create_task(operation())
        self._pending_storage = task
        # Retrieve failures even when the requesting hook has timed out. The
        # awaited task still raises normally for a caller that remains present.
        task.add_done_callback(
            lambda completed: None if completed.cancelled() else completed.exception()
        )
        await asyncio.shield(task)

    @property
    def undelivered_outcome_count(self) -> int:
        return sum(1 for entry in self._entries.values() if entry.get("state") == "received")

    def diagnostics(self) -> dict[str, Any]:
        awaiting = sum(entry.get("state") == "awaiting_host" for entry in self._entries.values())
        return {
            "awaiting_host": awaiting,
            "received": self.undelivered_outcome_count,
            "awaiting_host_limit": _MAX_AWAITING_HOST,
            "delivery_slots_limit": _MAX_ENTRIES,
            "reserved_delivery_slots": len(self._entries),
            "awaiting_host_near_capacity": awaiting * 5 >= _MAX_AWAITING_HOST * 4,
            "delivery_slots_near_capacity": len(self._entries) * 5 >= _MAX_ENTRIES * 4,
        }

    def _warn_backlogs(self) -> None:
        health = self.diagnostics()
        active = {
            name for name in ("awaiting_host", "delivery_slots")
            if health[f"{name}_near_capacity"]
        }
        for name in active - self._warned_backlogs:
            _LOGGER.warning(
                "native_hook_outcome_backlog_near_capacity queue=%s awaiting_host=%d received=%d",
                name, health["awaiting_host"], health["received"],
            )
        self._warned_backlogs = active

    async def _commit(self, entries: dict[str, dict[str, Any]]) -> None:
        # Publish the in-memory transition only after durable storage succeeds.
        async def persist() -> None:
            await asyncio.to_thread(self._write_sync, entries)
            self._entries = entries
            self._warn_backlogs()

        await self._storage(persist)

    async def _prune_locked(self) -> None:
        now = int(time.time())
        retained = {
            key: entry for key, entry in self._entries.items()
            if not (
                entry.get("state") == "awaiting_host"
                and int(entry.get("created_at") or 0) + _CORRELATION_TTL_SECONDS <= now
            )
        }
        if len(retained) != len(self._entries):
            await self._commit(retained)

    async def load(self) -> None:
        async def read() -> None:
            await asyncio.to_thread(self._load_sync)

        async with self._serialized():
            await self._storage(read)
            await self._prune_locked()
            self._warn_backlogs()

    async def reserve(
        self,
        action_key: str,
        binding_fingerprint: str,
        *,
        host_correlation: str | None = None,
        postflight_binding_fingerprint: str | None = None,
    ) -> None:
        host_correlation = str(host_correlation or action_key).strip()
        if (
            not action_key
            or len(binding_fingerprint) != 64
            or (postflight_binding_fingerprint is not None and len(postflight_binding_fingerprint) != 64)
            or not host_correlation
            or len(host_correlation) > 1024
        ):
            raise ValueError("hook outcome correlation is invalid")
        async with self._serialized():
            await self._prune_locked()
            if action_key in self._entries:
                raise ValueError("hook outcome correlation already exists")
            if self.diagnostics()["awaiting_host"] >= _MAX_AWAITING_HOST:
                raise ValueError("hook outcome awaiting-host queue is full")
            if len(self._entries) >= _MAX_ENTRIES:
                raise ValueError("hook outcome outbox is full")
            entry = {
                "state": "awaiting_host",
                "binding_fingerprint": binding_fingerprint,
                "postflight_binding_fingerprint": postflight_binding_fingerprint or binding_fingerprint,
                "host_correlation": host_correlation,
                "created_at": int(time.time()),
            }
            await self._commit({**self._entries, action_key: entry})

    async def record_outcome(
        self, action_key: str, outcome: Mapping[str, Any], *,
        observed_action_fingerprint: str | None = None,
    ) -> None:
        async with self._serialized():
            entry = self._entries.get(action_key)
            if not isinstance(entry, dict) or entry.get("state") != "awaiting_host":
                raise HookOutcomeCorrelationUnavailable("hook outcome correlation is unavailable")
            if observed_action_fingerprint is not None:
                expected = entry.get("postflight_binding_fingerprint", entry.get("binding_fingerprint"))
                if not isinstance(expected, str) or not hmac.compare_digest(expected, observed_action_fingerprint):
                    raise HookOutcomeBindingMismatch("hook outcome input binding differs")
            updated = {
                **entry,
                "state": "received",
                "outcome": dict(outcome),
                "received_at": int(time.time()),
            }
            await self._commit({**self._entries, action_key: updated})

    async def action_key_for_host_correlation(self, host_correlation: str) -> str:
        """Resolve a host event to its original grant-scoped action identity."""

        correlation = str(host_correlation or "").strip()
        async with self._serialized():
            matches = [
                action_key
                for action_key, entry in self._entries.items()
                if entry.get("host_correlation") == correlation
            ]
        if len(matches) != 1:
            raise HookOutcomeCorrelationUnavailable("hook outcome correlation is unavailable")
        return matches[0]

    async def received_entries(self) -> tuple[tuple[str, dict[str, Any]], ...]:
        async with self._serialized():
            return tuple(
                (key, dict(entry))
                for key, entry in self._entries.items()
                if entry.get("state") == "received"
            )

    async def acknowledge(self, action_key: str) -> None:
        async with self._serialized():
            if action_key in self._entries:
                await self._commit({
                    key: entry for key, entry in self._entries.items() if key != action_key
                })

    async def prune(self) -> None:
        async with self._serialized():
            await self._prune_locked()

    def _load_sync(self) -> None:
        if not self.path.exists():
            self._entries = {}
            return
        metadata = self.path.stat()
        if (
            self.path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _MAX_FILE_BYTES
        ):
            raise ValueError("hook outcome outbox is unsafe")
        if os.name == "nt":
            # POSIX uid/mode checks are unavailable on Windows. Restrict the
            # existing file to the same current-user DACL as its parent before
            # reading it, including files created by an earlier client build.
            from .windows_named_pipe import _secure_user_directory

            _secure_user_directory(self.path)
        elif metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError("hook outcome outbox is unsafe")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("hook outcome outbox is invalid") from exc
        raw_entries = payload.get("entries") if isinstance(payload, dict) else None
        if (
            payload.get("version") != _OUTBOX_FORMAT_VERSION
            or not isinstance(raw_entries, dict)
        ):
            raise ValueError("hook outcome outbox is invalid")
        if len(raw_entries) > _MAX_ENTRIES:
            raise ValueError("hook outcome outbox is invalid")
        self._entries = {}
        for key, entry in raw_entries.items():
            host_correlation = entry.get("host_correlation") if isinstance(entry, dict) else None
            if (
                not isinstance(key, str)
                or not isinstance(entry, dict)
                or not isinstance(host_correlation, str)
                or not host_correlation
                or len(host_correlation) > 1024
            ):
                raise ValueError("hook outcome outbox is invalid")
            self._entries[key] = dict(entry)
        if len(self._entries) != len(raw_entries):
            raise ValueError("hook outcome outbox is invalid")

    def _write_sync(self, entries: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        serialized = json.dumps(
            {"version": _OUTBOX_FORMAT_VERSION, "entries": entries},
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
