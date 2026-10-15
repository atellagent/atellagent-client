"""Run selected MCP bridges in isolated tasks with paired owned-resource cleanup."""

import asyncio
import logging
import time

from atellagent_client.onboarding.device_mcp_context import DeviceMCPContext
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.mcp_runtime_selection import MCPRuntimeSelections
from atellagent_client.onboarding.mcp_runtime_status import MCPRuntimeStatus
from .owned_tasks import join_owned

logger = logging.getLogger(__name__)


class MCPRuntimeReconciler:
    def __init__(self, directory):
        self.directory = directory
        self.selections, self.status = None, None
        self.active, self.failed, self.retry_after = {}, {}, {}
        self.participants, self.observed_at = {}, {}
        self.invalid = False

    def _publish(self, request_id, *, state, code=None, instance_id=None):
        try:
            if self.status is None:
                self.status = MCPRuntimeStatus(self.directory / "mcp-runtime-status")
            self.status.save(request_id, state=state, code=code, instance_id=instance_id)
        except (OnboardingClientError, OSError, ValueError):
            logger.error("Local MCP process observation unavailable (request=%s)", request_id)

    async def _run(self, request_id, selection):
        async with await DeviceMCPContext.open(self.directory) as context:
            flow = await asyncio.to_thread(context.flow, request_id)
            draft = flow.draft
            inputs = context.local_inputs.load(draft)
            context.selections.require_draft(selection, draft, inputs, context.receipts.load(draft))
            if context.selections.load().get(request_id) != selection:
                return
            async with context.open_runtime(request_id) as participant:
                self.participants[request_id] = participant
                self._publish(request_id, state="running", instance_id=participant.instance_id)
                self.observed_at[request_id] = time.monotonic()
                logger.warning("Local MCP bridge registered (request=%s, instance=%s)", request_id, participant.instance_id)
                # The shared participant owns receiving, heartbeat and renewal tasks.
                await asyncio.Event().wait()

    async def _stop(self, request_id):
        active = self.active.get(request_id)
        if active is not None:
            task = active[1]
            if not task.done() and not task.cancelling():
                task.cancel()
            # An interrupted reconciliation must not cancel this cleanup again
            # or lose the owner before close() can finish joining it.
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))
            if self.active.get(request_id) is active:
                self.active.pop(request_id)
        self.participants.pop(request_id, None)
        self.observed_at.pop(request_id, None)
        self._publish(request_id, state="stopped")

    async def reconcile(self):
        try:
            if self.selections is None:
                self.selections = MCPRuntimeSelections(self.directory / "mcp-runtime-selections")
            selected = self.selections.load()
        except (OnboardingClientError, OSError, ValueError):
            for request_id in tuple(self.active):
                await self._stop(request_id)
            if not self.invalid:
                logger.error("MCP selection unavailable; bridges stopped, agent controllers/reporting remain up")
            self.invalid = True
            return
        self.invalid = False
        for request_id, (selection, task) in tuple(self.active.items()):
            if selected.get(request_id) != selection:
                await self._stop(request_id)
            elif task.done():
                self.active.pop(request_id)
                self.participants.pop(request_id, None)
                error = None if task.cancelled() else task.exception()
                self._publish(request_id, state="failed", code=getattr(error, "code", "onboarding_mcp_runtime_failed"))
                if error is not None:
                    logger.error("Local MCP bridge failed (request=%s, error=%s, code=%s)",
                        request_id, type(error).__name__, getattr(error, "code", "unavailable"))
                if isinstance(error, OnboardingClientError) and error.retryable:
                    self.retry_after[request_id] = (selection, time.monotonic() + 30)
                else:
                    self.failed[request_id] = selection
            elif request_id in self.participants and time.monotonic() - self.observed_at[request_id] >= 30:
                self._publish(request_id, state="running", instance_id=self.participants[request_id].instance_id)
                self.observed_at[request_id] = time.monotonic()
        for request_id in tuple(self.failed):
            if selected.get(request_id) != self.failed[request_id]:
                self.failed.pop(request_id)
        for request_id, selection in selected.items():
            if request_id in self.active or self.failed.get(request_id) == selection:
                continue
            retry = self.retry_after.get(request_id)
            if retry is not None and retry[0] == selection and time.monotonic() < retry[1]:
                continue
            self.retry_after.pop(request_id, None)
            self._publish(request_id, state="starting")
            task = asyncio.create_task(self._run(request_id, selection), name=f"mcp-bridge-{request_id}")
            self.active[request_id] = (selection, task)

    async def close(self):
        await join_owned(*(self._stop(request_id) for request_id in tuple(self.active)), cancel_on_interrupt=False)
