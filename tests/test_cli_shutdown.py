# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Tests for graceful connected-participant CLI termination."""

from __future__ import annotations

import asyncio
import importlib
import signal
import unittest
from unittest.mock import patch

cli_main = importlib.import_module("atellagent_client.cli.main")


class _SignalLoop:
    def __init__(self) -> None:
        self.handlers: dict[signal.Signals, object] = {}
        self.removed: list[signal.Signals] = []

    def add_signal_handler(self, signum: signal.Signals, callback: object) -> None:
        self.handlers[signum] = callback

    def remove_signal_handler(self, signum: signal.Signals) -> bool:
        self.removed.append(signum)
        return self.handlers.pop(signum, None) is not None


class CliShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_sigterm_unblocks_and_removes_handlers(self) -> None:
        loop = _SignalLoop()
        with patch.object(cli_main.asyncio, "get_running_loop", return_value=loop):
            task = asyncio.create_task(cli_main._wait_for_shutdown_signal())
            await asyncio.sleep(0)
            callback = loop.handlers[signal.SIGTERM]
            self.assertTrue(callable(callback))
            callback()
            await task
        self.assertEqual({signal.SIGTERM, signal.SIGINT}, set(loop.removed))


if __name__ == "__main__":
    unittest.main()
