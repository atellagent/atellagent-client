"""Explicit native actions; continuation never accepts replacement config/key data."""

import argparse
import re
from pathlib import Path
from uuid import UUID


def options(arguments):
    parser = argparse.ArgumentParser(description="Request or resume a native integration on the enrolled device. Request submission authorizes agent.control on this device until revoked; administrator approval remains required.",
        epilog="Read-only recovery inventory: atellagent-cli setup native saved --json [--after <request-id>]")
    commands = parser.add_subparsers(dest="action", required=True)
    request = commands.add_parser("request")
    request.add_argument("--host", required=True, choices=("codex", "claude-code"))
    request.add_argument("--name", required=True)
    request.add_argument("--packaging", choices=("sdk", "bridge"), default="sdk")
    request.add_argument("--enforcement-mode", choices=("enforce", "observe"), default="enforce")
    request.add_argument("--coverage-posture", choices=("strict", "permissive"), default="strict")
    request.add_argument("--wait-seconds", type=int, default=60)
    for action in ("resume", "submit", "consent", "select", "pickup"):
        command = commands.add_parser(action)
        command.add_argument("request_id")
        command.add_argument("--wait-seconds", type=int, default=60)
    unselect = commands.add_parser("unselect", help="Remove an exact local controller selection; retain credentials")
    unselect.add_argument("request_id")
    hooks = commands.add_parser("hooks", help="Preview generated settings for an exact selected controller")
    hooks.add_argument("request_id")
    hooks.add_argument("--json", action="store_true", required=True)
    hooks.add_argument("--remove", action="store_true", help="Review removal of exact generated hook settings")
    hooks.add_argument("--apply-review", help="Save settings only if this exact preview hash still matches")
    runtime = commands.add_parser("run", help="Run an activated native controller from its approved request")
    runtime.add_argument("request_id")
    runtime.add_argument("--hook-control-socket", required=True)
    runtime.add_argument("--installation-directory", type=Path,
        help="Absolute owner-private enrolled installation for this foreground run only; "
             "does not change the installed service or saved host hooks")
    runtime.add_argument("--verify", action="store_true")
    args = parser.parse_args(arguments)
    if args.action == "run" and args.installation_directory is not None and not args.installation_directory.is_absolute():
        parser.error("--installation-directory must be an absolute enrolled installation path")
    if args.action == "hooks" and args.apply_review is not None and not re.fullmatch(r"[0-9a-f]{64}", args.apply_review):
        parser.error("--apply-review must be the exact lowercase SHA-256 from the settings preview")
    if args.action not in {"run", "unselect", "hooks"} and not 1 <= args.wait_seconds <= 900:
        parser.error("--wait-seconds must be between 1 and 900")
    if args.action != "request":
        try:
            identifier = UUID(args.request_id)
            if str(identifier) != args.request_id:
                raise ValueError("canonical request ID required")
            args.request_id = identifier
        except ValueError:
            parser.error("request_id must be a canonical UUID")
    return args
