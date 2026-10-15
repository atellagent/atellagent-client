"""Reconcile exact local selections without restarting the device reporter."""

import asyncio
import logging
import time

from .owned_tasks import join_owned

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_runtime_selection import HOSTS
from atellagent_client.onboarding.native_setup_context import NativeSetupContext
from atellagent_client.onboarding.native_runtime_config import open_native_runtime

logger = logging.getLogger(__name__)


class NativeControllerReconciler:
    def __init__(self, directory, selections, runtime_factory, endpoint):
        self.directory, self.selections = directory, selections
        self.runtime_factory, self.endpoint = runtime_factory, endpoint
        self.active, self.failed, self.retry_after = {}, {}, {}
        self.invalid = set()

    async def _stop(self, host):
        if host in self.active:
            # Do not start a replacement unless the old runtime has stopped.
            await self.active[host][1].stop()
            del self.active[host]

    async def reconcile(self):
        await join_owned(*(self._reconcile_host(host) for host in HOSTS))

    async def _reconcile_host(self, host):
        try:
            selection = self.selections.load(host)
        except (OnboardingClientError, OSError, ValueError):
            await self._stop(host)
            self.failed.pop(host, None)
            self.retry_after.pop(host, None)
            if host not in self.invalid:
                logger.error("Local %s selection is unavailable; controller stopped", host)
                self.invalid.add(host)
            return
        self.invalid.discard(host)
        active = self.active.get(host)
        if active is not None and active[0] != selection:
            await self._stop(host)
        if selection is None:
            self.failed.pop(host, None)
            self.retry_after.pop(host, None)
            return
        if host in self.active or self.failed.get(host) == selection:
            return
        retry = self.retry_after.get(host)
        if retry is not None and retry[0] == selection and time.monotonic() < retry[1]:
            return
        runtime = None
        try:
            async with await NativeSetupContext.open(self.directory) as context:
                draft = await asyncio.to_thread(context.load, selection["request_id"])
                self.selections.require_draft(selection, draft)
                setup = await open_native_runtime(context, selection["request_id"])
            # A selection removed/changed during network verification must not start.
            if self.selections.load(host) != selection:
                return
            if setup.config.native_host != host:
                raise OnboardingClientError("onboarding_native_selection_invalid")
            runtime = self.runtime_factory(setup.config, native_binding=setup.binding,
                                           socket_path=self.endpoint(host))
            self.active[host] = (selection, runtime)
            await runtime.start()
        except Exception as exc:
            logger.error("Local %s controller failed to start (stage=%s, error=%s, code=%s)",
                host, getattr(runtime, "startup_stage", "unknown"), type(exc).__name__,
                getattr(exc, "code", "unavailable"))
            await self._stop(host)
            if isinstance(exc, OnboardingClientError) and exc.retryable:
                self.retry_after[host] = (selection, time.monotonic() + 30)
            else:
                self.failed[host] = selection
        else:
            self.failed.pop(host, None)
            self.retry_after.pop(host, None)
            logger.warning("Local %s controller ready at %s", host, runtime.socket_path)

    async def close(self):
        async def stop(host):
            try:
                await self._stop(host)
            except Exception:
                logger.error("Local %s controller cleanup failed", host)
        await join_owned(*(stop(host) for host in tuple(self.active)), cancel_on_interrupt=False)
