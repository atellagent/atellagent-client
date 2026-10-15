"""Graceful foreground runtime termination, independent of command dispatch."""

import asyncio
from contextlib import suppress
import signal


async def wait_for_shutdown_signal():
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    registered = []
    for termination_signal in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(termination_signal, stopped.set)
        except (NotImplementedError, RuntimeError):
            continue
        registered.append(termination_signal)
    try:
        await stopped.wait()
    finally:
        for termination_signal in registered:
            with suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(termination_signal)
