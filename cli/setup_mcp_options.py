"""Argument parsing only for device-associated MCP connection commands."""

import argparse
from pathlib import Path
from uuid import UUID


def options(arguments):
    parser = argparse.ArgumentParser(prog="atellagent-cli setup mcp",
        description="Review, request and run a local MCP server on this enrolled device.")
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("request", "submit", "resume", "review", "start", "stop", "run", "saved",
                   "bootstrap", "bootstraps", "preparations", "pickup"):
        command = commands.add_parser(action)
        if action in {"submit", "resume", "review", "start", "stop", "run"}:
            command.add_argument("request_id", type=UUID)
        if action in {"preparations", "pickup"}:
            command.add_argument("session_id", type=UUID)
        if action == "pickup":
            command.add_argument("assignment_id", type=UUID)
        if action in {"saved", "bootstraps", "preparations"}:
            command.add_argument("--after", type=UUID)
        if action in {"request", "pickup"}:
            command.add_argument("--host", choices=("codex", "claude-code"))
            target = command.add_mutually_exclusive_group(required=True)
            target.add_argument("--discovered-name")
            target.add_argument("--target-url", help="Local streamable HTTP MCP URL")
            target.add_argument("--target-command", help="Executable path or command on PATH")
            command.add_argument("--target-arg", action="append", default=[], help="One executable argument; use --target-arg=value for leading dashes")
            command.add_argument("--source")
            command.add_argument("--project", type=Path)
            command.add_argument("--credential-env", action="append", default=[], metavar="TARGET=HOST_ENVIRONMENT_NAME")
            command.add_argument("--http-token-env", help="Environment name containing the complete HTTP credential header value")
            command.add_argument("--http-auth-header", help="HTTP credential header name (default: Authorization)")
        if action == "request":
            command.add_argument("--name", required=True)
        if action == "bootstrap":
            command.add_argument("--name")
            command.add_argument("--session-id", type=UUID, help="Resubmit the exact retained key reservation")
        if action in {"request", "submit", "resume", "pickup"}:
            command.add_argument("--wait-seconds", type=int, default=60)
        if action in {"start", "run"}:
            command.add_argument("--reviewed-inputs-sha256", help="Digest returned by local target review")
        if action in {"saved", "bootstraps", "review", "preparations"}:
            command.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    if args.action in {"request", "pickup"}:
        if args.discovered_name and not args.host:
            parser.error("A discovered target requires --host")
        if not args.discovered_name and (args.host or args.source or args.project):
            parser.error("Host, source and project apply only to discovered targets")
        if args.target_arg and not args.target_command:
            parser.error("--target-arg requires --target-command")
    if args.action == "bootstrap" and bool(args.name) == bool(args.session_id):
        parser.error("bootstrap requires either --name for a new reservation or --session-id to resubmit one")
    if hasattr(args, "wait_seconds") and not 1 <= args.wait_seconds <= 900:
        parser.error("--wait-seconds must be between 1 and 900")
    return args


def credential_mapping(values):
    result = {}
    for value in values:
        target, separator, source = value.partition("=")
        if not separator or not target or not source or target in result:
            raise ValueError("Use unique TARGET=HOST_ENVIRONMENT_NAME mappings, not credential values")
        result[target] = source
    return result
