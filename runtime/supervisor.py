"""Device and native-controller lifecycle, independent of command shells."""

import asyncio

from atellagent_client.integrations.agents.hook_control import HookControlRuntime
from atellagent_client.onboarding.device_runtime import DeviceRuntime
from atellagent_client.onboarding.native_runtime_selection import NativeRuntimeSelections
from .native_reconciliation import NativeControllerReconciler
from .mcp_reconciliation import MCPRuntimeReconciler
from .owned_tasks import join_owned


async def _reconcile(controllers, mcp, shutdown):
    # Shutdown may already be ready before this owned task first runs.
    if not shutdown.done():
        await join_owned(controllers.reconcile(), mcp.reconcile())


async def _finish_shutdown(reconciliation, shutdown, mcp, controllers):
    """Retain every cleanup phase even if the supervisor is interrupted again."""
    errors = []
    for task in [reconciliation, shutdown]:
        if task is None:
            continue
        if not task.done() and not task.cancelling():
            task.cancel()
        results = await asyncio.gather(task, return_exceptions=True)
        errors.extend(value for value in results if isinstance(value, Exception))
    try:
        await join_owned(mcp.close(), controllers.close(), cancel_on_interrupt=False)
    except Exception as exc:
        errors.append(exc)
    if errors:
        for other in errors[1:]:
            errors[0].add_note(f"Other shutdown work also failed ({type(other).__name__}).")
        raise errors[0]


async def run(directory, *, endpoint, wait_for_shutdown, runtime_factory=HookControlRuntime):
    async with await DeviceRuntime.open(directory):
        selections = NativeRuntimeSelections(directory / "native-runtime-selections")
        controllers = NativeControllerReconciler(directory, selections, runtime_factory, endpoint)
        mcp = MCPRuntimeReconciler(directory)
        shutdown = asyncio.create_task(wait_for_shutdown())
        reconciliation = None
        try:
            while not shutdown.done():
                reconciliation = asyncio.create_task(_reconcile(controllers, mcp, shutdown),
                    name="device-service-reconciliation")
                await asyncio.wait({shutdown, reconciliation}, return_when=asyncio.FIRST_COMPLETED)
                if reconciliation.done():
                    # Preserve a real failure even when shutdown becomes ready
                    # in the same scheduling window.
                    await reconciliation
                    reconciliation = None
                if shutdown.done():
                    break
                await asyncio.wait({shutdown}, timeout=2.0)
            await shutdown
        finally:
            await join_owned(
                _finish_shutdown(reconciliation, shutdown, mcp, controllers),
                cancel_on_interrupt=False,
            )
