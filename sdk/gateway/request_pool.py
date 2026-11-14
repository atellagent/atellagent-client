"""Exclusive, short-lived transport loans; no credentials or decisions cached."""
from contextlib import asynccontextmanager
from dataclasses import dataclass
import asyncio
import os
import time


@dataclass
class _Idle:
    client: object
    created: float
    returned: float
    generation: tuple


class RequestClientPool:
    MAX_IDLE = 4
    MAX_AGE = 2.0
    MAX_IDLE_AGE = 2.0

    def __init__(self, factory, generation):
        self._factory, self._generation = factory, generation
        self._loop = asyncio.get_running_loop()
        self._idle = []
        self._active = {}
        self._closing = {}
        self.closed = False

    def _check(self):
        if self.closed or asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("Gateway request pool is closed or belongs to another event loop")

    async def _close_clients(self, clients):
        tasks=[]
        for client in clients:
            key=id(client)
            task=self._closing.get(key)
            if task is None:
                task=asyncio.create_task(client.aclose())
                self._closing[key]=task
                def finished(future,key=key):
                    self._closing.pop(key,None)
                    if not future.cancelled():future.exception()
                task.add_done_callback(finished)
            tasks.append(task)
        if tasks:
            await asyncio.gather(*(asyncio.shield(task)for task in tasks))

    @asynccontextmanager
    async def borrow(self):
        self._check()
        try:
            generation = (os.getpid(), self._generation())
        except Exception:
            idle,self._idle=self._idle,[]
            await self._close_clients([item.client for item in idle])
            raise
        now = time.monotonic()
        chosen = None
        stale = []
        while self._idle:
            item = self._idle.pop()
            if (item.generation == generation and 0 <= now-item.created < self.MAX_AGE
                    and 0 <= now-item.returned < self.MAX_IDLE_AGE):
                chosen = item
                break
            stale.append(item.client)
        if chosen is None:
            chosen = _Idle(self._factory(), time.monotonic(), time.monotonic(), generation)
        client = chosen.client
        self._active[id(client)] = client
        success = False
        try:
            # Own the chosen client before yielding for stale-client cleanup.
            await self._close_clients(stale)
            self._check()
            # Each request keeps its own new headers/auth/device proof. Cookies
            # learned from a previous response must not cross request ownership.
            client.cookies.clear()
            yield client
            success = True
        finally:
            self._active.pop(id(client), None)
            now = time.monotonic()
            try:
                unchanged = generation == (os.getpid(), self._generation())
            except (OSError, RuntimeError):
                unchanged = False
            if (success and not self.closed and not client.is_closed
                    and unchanged
                    and 0 <= now-chosen.created < self.MAX_AGE
                    and len(self._idle) < self.MAX_IDLE):
                chosen.returned = now
                self._idle.append(chosen)
            else:
                await self._close_clients([client])

    async def aclose(self):
        self.closed = True
        clients = [item.client for item in self._idle]+list(self._active.values())
        self._idle.clear()
        self._active.clear()
        await self._close_clients(clients)
        if self._closing:
            await asyncio.gather(*(asyncio.shield(task)for task in list(self._closing.values())))
