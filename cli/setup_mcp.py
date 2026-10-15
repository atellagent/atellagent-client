"""Thin attended/foreground CLI shell over shared device-associated MCP APIs."""

import asyncio
import json
import sys

from atellagent_client.connected import MCPTargetError
from atellagent_client.sdk import DeviceMCPContext, MCPExistingRequestError, OnboardingClientError, SetupManagementClient
from atellagent_client.onboarding.installation_paths import installation_directory
from .setup_login import login
from .setup_mcp_options import options, credential_mapping
from .setup_mcp_target import selected_target
from .setup_mcp_progress import wait_for_mcp


async def run(arguments, *, interaction=None):
    args = options(arguments)
    request_id = getattr(args, "request_id", None)
    try:
        if args.action == "stop":
            DeviceMCPContext.stop_selected(installation_directory(), request_id)
            _event(interaction, "mcp_stopped", request_id)
            return
        async with await DeviceMCPContext.open(installation_directory()) as context:
            if args.action == "bootstraps":
                print(json.dumps(context.bootstrap_setups(after=args.after), sort_keys=True), flush=True)
                return
            if args.action == "saved":
                print(json.dumps(await context.saved(after=args.after), sort_keys=True), flush=True)
                return
            if args.action in {"bootstrap", "preparations", "pickup"}:
                from .setup_mcp_preparations import run_preparation
                request_id = await run_preparation(context, args, interaction=interaction)
                if request_id is None:
                    return
            elif args.action in {"request", "submit"}:
                origin = context.device_context.setup.platform_origin
                tokens = await login(origin, interaction=interaction)
                async with SetupManagementClient(origin=origin, tokens=tokens) as management:
                    if args.action == "request":
                        target = selected_target(args)
                        draft = await context.prepare_target(management, target=target, display_name=args.name,
                            credential_environment_names=credential_mapping(args.credential_env),
                            http_token_environment=args.http_token_env, http_auth_header=args.http_auth_header)
                        request_id = draft.proposal.id
                        _event(interaction, "mcp_request_saved", request_id)
                    await context.submit_request(management, request_id)
                    _event(interaction, "mcp_request_submitted", request_id)
            if args.action in {"review", "start", "run"}:
                review = context.review(request_id)
                if args.action == "review":
                    print(json.dumps(review, sort_keys=True), flush=True)
                    return
                digest = args.reviewed_inputs_sha256
                if digest is None:
                    if interaction is not None:
                        accepted = await interaction.confirm_mcp(review)
                    else:
                        print(json.dumps(review, indent=2), flush=True)
                        print("Starting may launch this executable or contact the local endpoint. No software is installed.")
                        accepted = sys.stdin.isatty() and await asyncio.to_thread(input, "Type the connection name to start: ") == review["display_name"]
                    if not accepted:
                        _event(interaction, "mcp_start_cancelled", request_id)
                        return
                    digest = review["local_inputs_sha256"]
                if digest != review["local_inputs_sha256"]:
                    raise OnboardingClientError("onboarding_mcp_local_review_changed")
                if args.action == "start":
                    await context.select(request_id, reviewed_local_inputs_sha256=digest)
                    _event(interaction, "mcp_selected", request_id)
                else:
                    from .shutdown import wait_for_shutdown_signal
                    async with context.open_runtime(request_id) as participant:
                        print(f"MCP participant registered: {participant.instance_id}", flush=True)
                        await wait_for_shutdown_signal()
                return
            await wait_for_mcp(context.flow(request_id), args.wait_seconds, interaction=interaction)
    except (OnboardingClientError, OSError, ValueError, EOFError) as exc:
        if isinstance(exc, MCPExistingRequestError):
            _event(interaction, "mcp_existing_request", exc.request_id)
        code = exc.code if isinstance(exc, (OnboardingClientError, MCPTargetError)) else "onboarding_mcp_local_target_invalid"
        if interaction is not None:
            interaction.emit("error", code=code)
        else:
            print(f"[ERROR] {code}. {str(exc) if isinstance(exc, ValueError) else 'Existing requests and credentials were retained.'}", file=sys.stderr)
            if request_id is not None:
                print(f"Continue saved request: atellagent-cli setup mcp resume {request_id}", file=sys.stderr)
        raise SystemExit(1) from None


def _event(interaction, event, request_id):
    if interaction is not None:
        interaction.emit(event, request_id=str(request_id))
    else:
        print(f"{event}: {request_id}", flush=True)
