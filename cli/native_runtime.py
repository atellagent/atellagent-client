"""Foreground native controller from an exact approved request; no YAML or login."""

from atellagent_client.integrations.agents.hook_control import HookControlRuntime
from atellagent_client.onboarding.native_runtime_config import open_native_runtime
from .shutdown import wait_for_shutdown_signal


async def run(context, request_id, *, socket_path, verify=False):
    setup = await open_native_runtime(context, request_id)
    runtime = HookControlRuntime(setup.config, native_binding=setup.binding, socket_path=socket_path)
    try:
        await runtime.start()
        print(f"Native controller listening: {runtime.socket_path}", flush=True)
        if not verify:
            await wait_for_shutdown_signal()
    finally:
        await runtime.stop()
