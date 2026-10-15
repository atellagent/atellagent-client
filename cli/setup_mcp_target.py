"""Resolve a CLI target selection through shared local target constructors."""

from pathlib import Path

from atellagent_client.discovery.targets import select_target
from atellagent_client.onboarding.mcp_manual_target import manual_target
from .setup_mcp_options import credential_mapping


def selected_target(args):
    if args.discovered_name:
        return select_target(host=args.host, name=args.discovered_name, home=Path.home(),
            project=args.project, source=args.source)
    return manual_target(url=args.target_url, command=args.target_command,
        arguments=args.target_arg, credential_environment_names=credential_mapping(args.credential_env))
