"""Exact saved workload commands; local targets never substitute identity settings."""

import argparse
from pathlib import Path
from uuid import UUID


def options(arguments):
    parser = argparse.ArgumentParser(description="Resume, configure or run an approved workload.")
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("request", "submit", "resume", "configure", "run", "verify",
                   "bootstrap", "bootstrap-submit", "preparations", "pickup"):
        command = commands.add_parser(action)
        if action in {"bootstrap-submit", "preparations", "pickup"}: command.add_argument("session_id", type=UUID)
        elif action not in {"request", "bootstrap"}: command.add_argument("request_id", type=UUID)
        if action == "pickup": command.add_argument("assignment_id", type=UUID)
        if action in {"preparations", "pickup"}: command.add_argument("--after", type=UUID)
        if action == "preparations": command.add_argument("--json", action="store_true")
        command.add_argument("--state-directory", required=True, type=Path,
                             help="Owner-private saved workload state directory")
        if action in {"request", "submit", "resume"}: command.add_argument("--wait-seconds", type=int, default=60)
        if action in {"request", "bootstrap"}:
            command.add_argument("--platform-url", required=True)
            command.add_argument("--name", required=True)
        if action == "request":
            command.add_argument("--type", dest="account_type", required=True,
                choices=("agent", "workflow_runtime", "model", "ml_filter", "mcp_server", "channel"))
            command.add_argument("--packaging", choices=("sdk", "bridge"), default="sdk")
            command.add_argument("--capability", action="append", required=True)
            command.add_argument("--identity-mode", choices=("boundary_identity_only", "federated_agent_identity"))
            command.add_argument("--control-source", choices=("cluster_directive", "local_manifest"), default="cluster_directive")
            command.add_argument("--allow-direct-mcp-clients", action="store_true", help="Allow native-authenticated direct MCP clients to request central policy authorization")
            command.add_argument("--local-guardrail-mode", choices=("observe", "enforce"))
            command.add_argument("--offline-dispatch-mode", choices=("queue", "fail_immediately"), default="queue")
            command.add_argument("--offline-dispatch-deadline-seconds", type=int)
            command.add_argument("--channel-type")
            command.add_argument("--filter-execution-boundary", choices=("model_boundary", "tool_response", "egress"))
        if action in {"request", "configure"}:
            command.add_argument("--handler")
            command.add_argument("--mcp-manifest")
            command.add_argument("--guardrail-manifest")
            command.add_argument("--target-idempotent", action="store_true")
            command.add_argument("--target-transport", choices=("handler", "http", "stdio"), default="handler")
            command.add_argument("--target-url")
            command.add_argument("--target-unix-socket")
            command.add_argument("--target-command")
            command.add_argument("--target-arg", action="append", default=[])
            command.add_argument("--target-env", action="append", default=[], metavar="NAME=ENVIRONMENT_NAME")
            command.add_argument("--upstream-auth-header")
            command.add_argument("--upstream-token-env")
            for name in ("cert", "key", "ca"): command.add_argument(f"--upstream-{name}-path")
    args = parser.parse_args(arguments)
    if not args.state_directory.is_absolute(): parser.error("--state-directory must be absolute")
    if args.action in {"request", "submit", "resume"} and not 1 <= args.wait_seconds <= 900:
        parser.error("--wait-seconds must be between 1 and 900")
    return args


def local_inputs(args, proposal):
    from atellagent_client.sdk import WorkloadLocalInputs
    deployment = {"type": proposal.packaging}
    targets = dict(target_transport=args.target_transport, target_url=args.target_url,
        target_unix_socket=args.target_unix_socket, target_command=args.target_command,
        target_args=args.target_arg, upstream_auth_header=args.upstream_auth_header,
        upstream_auth_token_env=args.upstream_token_env, upstream_cert_path=args.upstream_cert_path,
        upstream_key_path=args.upstream_key_path, upstream_ca_path=args.upstream_ca_path)
    env = {}
    for entry in args.target_env:
        name, separator, source = entry.partition("=")
        if not separator or not name or not source or name in env: raise ValueError("Unique NAME=ENVIRONMENT_NAME target mappings required")
        env[name] = source
    targets["target_env_map"] = env
    if proposal.packaging == "bridge": deployment.update(targets)
    elif any(value for name, value in targets.items() if name != "target_transport") or args.target_transport != "handler":
        raise ValueError("Bridge target options require approved bridge packaging")
    return WorkloadLocalInputs.from_payload(WorkloadLocalInputs(deployment,
        local_guardrail_manifest_path=args.guardrail_manifest, handler=args.handler,
        mcp_manifest_path=args.mcp_manifest, target_idempotent=args.target_idempotent).payload(), proposal=proposal)
