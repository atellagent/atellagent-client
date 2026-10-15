# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bounded callback waiting and durable outcome delivery."""

import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from atellagent_client.integrations.agents.hook_outbox import HookOutcomeOutbox

_MODULE = "atellagent_client.integrations.agents.hook_outbox"
_OUTCOME = {"outcome_observation": "result_observed", "success": None}


class HookOutcomeOutboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.box = HookOutcomeOutbox(Path(directory.name) / "outcomes.json")

    async def test_wait_capacity_and_delivery_reservations_are_separate(self):
        with patch(f"{_MODULE}._MAX_AWAITING_HOST", 2), patch(f"{_MODULE}._MAX_ENTRIES", 4):
            for key in ("received-1", "received-2"):
                await self.box.reserve(key, "a" * 64)
                await self.box.record_outcome(key, _OUTCOME)
            for key in ("waiting-1", "waiting-2"):
                await self.box.reserve(key, "b" * 64)
            with self.assertRaisesRegex(ValueError, "awaiting-host queue is full"):
                await self.box.reserve("overflow", "c" * 64)
            # Even with both budgets occupied, admitted outcomes can arrive.
            for key in ("waiting-1", "waiting-2"):
                await self.box.record_outcome(key, _OUTCOME)
            self.assertEqual(self.box.diagnostics()["received"], 4)
            with self.assertRaisesRegex(ValueError, "outbox is full"):
                await self.box.reserve("overflow", "c" * 64)
            await self.box.acknowledge("received-1")
            await self.box.reserve("next", "c" * 64)
            restored = HookOutcomeOutbox(self.box.path)
            await restored.load()
            self.assertEqual(restored.diagnostics(), self.box.diagnostics())

    async def test_expiration_frees_waits_but_preserves_received_evidence(self):
        with patch(f"{_MODULE}.time", Mock(time=Mock(return_value=1000))):
            await self.box.reserve("waiting", "a" * 64)
            await self.box.reserve("received", "a" * 64)
            await self.box.record_outcome("received", _OUTCOME)
        with patch(f"{_MODULE}.time", Mock(time=Mock(return_value=1900))):
            # Admission does not depend on the maintenance task getting a turn.
            await self.box.reserve("new", "a" * 64)
            self.assertEqual(self.box.diagnostics()["awaiting_host"], 1)
            self.assertEqual(self.box.diagnostics()["received"], 1)
            restored = HookOutcomeOutbox(self.box.path)
            await restored.load()
            self.assertEqual(restored.diagnostics(), self.box.diagnostics())

    async def test_storage_failure_keeps_previous_durable_state_retryable(self):
        await self.box.reserve("waiting", "a" * 64)
        original = self.box.path.read_bytes()
        with patch.object(self.box, "_write_sync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.box.record_outcome("waiting", _OUTCOME)
            with self.assertRaises(OSError):
                await self.box.reserve("uncommitted", "b" * 64)
        self.assertEqual(self.box.path.read_bytes(), original)
        self.assertEqual(self.box.diagnostics()["awaiting_host"], 1)
        self.assertEqual(self.box.diagnostics()["received"], 0)
        await self.box.record_outcome("waiting", _OUTCOME)
        with patch.object(self.box, "_write_sync", side_effect=OSError("disk full")), self.assertRaises(OSError):
            await self.box.acknowledge("waiting")
        self.assertEqual(self.box.diagnostics()["received"], 1)

    async def test_backlog_warning_is_before_capacity_and_not_repeated(self):
        with patch(f"{_MODULE}._MAX_AWAITING_HOST", 5), patch(f"{_MODULE}._LOGGER") as logger:
            for index in range(3):
                await self.box.reserve(str(index), "a" * 64)
            logger.warning.assert_not_called()
            await self.box.reserve("3", "a" * 64)
            self.assertTrue(self.box.diagnostics()["awaiting_host_near_capacity"])
            logger.warning.assert_called_once()
            await self.box.reserve("4", "a" * 64)
            logger.warning.assert_called_once()
            await self.box.acknowledge("4")
            await self.box.acknowledge("3")
            await self.box.reserve("again", "a" * 64)
            self.assertEqual(logger.warning.call_count, 2)

    async def test_cancelled_disk_write_cannot_overwrite_a_later_outcome(self):
        await self.box.reserve("first", "a" * 64)
        await self.box.reserve("second", "b" * 64)
        started = threading.Event()
        release = threading.Event()
        original_write = self.box._write_sync

        def blocked_write(entries):
            if entries["first"]["state"] == "received" and entries["second"]["state"] == "awaiting_host":
                started.set()
                if not release.wait(2):
                    raise TimeoutError("test did not release storage")
            original_write(entries)

        with patch.object(self.box, "_write_sync", side_effect=blocked_write):
            first = asyncio.create_task(self.box.record_outcome("first", _OUTCOME))
            second = None
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(first, 1)
                second = asyncio.create_task(self.box.record_outcome("second", _OUTCOME))
                # A second mutation must not pass an unfinished storage commit.
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(second), 0.05)
                abandoned = asyncio.create_task(self.box.reserve("abandoned", "c" * 64))
                await asyncio.sleep(0)
                abandoned.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await abandoned
            finally:
                release.set()
                if second is not None:
                    await second
        restored = HookOutcomeOutbox(self.box.path)
        await restored.load()
        self.assertEqual(restored.diagnostics()["received"], 2)
        self.assertEqual(restored.diagnostics()["reserved_delivery_slots"], 2)
        self.assertEqual(restored.diagnostics(), self.box.diagnostics())

    async def test_host_correlation_resolves_the_original_grant_scoped_action(self):
        await self.box.reserve(
            "local:grant-a:1:" + "a" * 64,
            "a" * 64,
            host_correlation="codex:session-1:tool-1",
        )

        assert await self.box.action_key_for_host_correlation(
            "codex:session-1:tool-1"
        ) == "local:grant-a:1:" + "a" * 64

    async def test_outbox_without_host_correlation_is_invalid(self):
        self.box.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.box.path.write_text(
            '{"version":1,"entries":{"legacy":{"state":"awaiting_host",'
            '"binding_fingerprint":"' + ("a" * 64) + '","created_at":1}}}',
            encoding="utf-8",
        )
        self.box.path.chmod(0o600)

        with self.assertRaisesRegex(ValueError, "outbox is invalid"):
            await self.box.load()

    async def test_windows_load_secures_existing_outbox_without_posix_uid(self):
        await self.box.reserve("waiting", "a" * 64)
        restored = HookOutcomeOutbox(self.box.path)
        with (
            patch(f"{_MODULE}.os.name", "nt"),
            patch(
                f"{_MODULE}.os.getuid",
                side_effect=AssertionError("POSIX uid used"),
                create=True,
            ),
            patch(
                "atellagent_client.integrations.agents.windows_named_pipe._secure_user_directory"
            ) as secure_file,
        ):
            await restored.load()
        secure_file.assert_called_once_with(self.box.path)
        self.assertEqual(restored.diagnostics()["awaiting_host"], 1)

    async def test_failed_cancelled_commit_preserves_state_and_allows_retry(self):
        await self.box.reserve("waiting", "a" * 64)
        started = threading.Event()
        release = threading.Event()

        def failed_write(_entries):
            started.set()
            if not release.wait(2):
                raise TimeoutError("test did not release storage")
            raise OSError("disk full")

        with patch.object(self.box, "_write_sync", side_effect=failed_write):
            write = asyncio.create_task(self.box.record_outcome("waiting", _OUTCOME))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                write.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(write, 1)
            finally:
                release.set()
            self.assertEqual(await self.box.received_entries(), ())
        self.assertEqual(self.box.diagnostics()["awaiting_host"], 1)
        await self.box.record_outcome("waiting", _OUTCOME)
        restored = HookOutcomeOutbox(self.box.path)
        await restored.load()
        self.assertEqual(restored.diagnostics()["received"], 1)


if __name__ == "__main__":
    unittest.main()
