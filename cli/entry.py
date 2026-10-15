"""Lightweight CLI dispatch keeps guarded hooks outside ordinary CLI imports."""

import sys


def main():
    arguments = sys.argv[1:]
    if arguments[:1] == ["mcp-bridge"]:
        from atellagent_client.proxy.cli import mcp_bridge_main
        return mcp_bridge_main(arguments[1:])
    if arguments[:1] == ["hook"]:
        from atellagent_client.hook_launcher import main as launch_hook
        return launch_hook(arguments[1:])
    if arguments[:1] == ["hook-worker"]:
        from atellagent_client.integrations.agents.host_hooks import main as run_hook
        return run_hook(arguments[1:])
    from .main import main as run_cli
    return run_cli()
