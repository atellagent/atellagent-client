"""Presentation for administrator-prepared MCP settings and retained pickup."""

import json

from atellagent_client.discovery.mcp_connection import target_inputs
from atellagent_client.sdk import OnboardingClientError, SetupManagementClient
from .setup_login import login
from .setup_mcp_options import credential_mapping
from .setup_mcp_target import selected_target


async def run_preparation(context, args, *, interaction=None):
    if args.action == "bootstrap":
        device = context.device_context.client.device
        origin = context.device_context.setup.platform_origin
        tokens = await login(origin, interaction=interaction)
        async with SetupManagementClient(origin=origin, tokens=tokens) as management:
            setup = context.load_bootstrap(args.session_id) if args.session_id is not None else await context.prepare_bootstrap(
                management, name=args.name, tenant_id=device.tenant_id, owner_user_id=device.owner_user_id)
            if interaction is None:
                print(f"Prepared-pickup setup saved: {setup.operation_id}", flush=True)
            else:
                interaction.emit("mcp_bootstrap_saved", session_id=str(setup.operation_id))
            await context.submit_bootstrap(management, setup.operation_id)
            if interaction is not None:
                interaction.emit("mcp_bootstrap_submitted", session_id=str(setup.operation_id))
            else:
                print("Key reservation submitted. An administrator can assign settings; integration approval covers the reservation.", flush=True)
        return None
    setup = context.load_bootstrap(args.session_id)
    if args.action == "preparations":
        page = await context.preparations(setup, after=args.after)
        items = [dict(id=str(item.id), display_name=item.proposal.display_name,
            account_type=item.proposal.account_type, packaging=item.proposal.packaging,
            status=item.status, settings_sha256=item.settings_sha256,
            expires_at=item.expires_at.isoformat(),
            proposed_request_id=None if item.proposed_request_id is None else str(item.proposed_request_id))
            for item in page.preparations]
        print(json.dumps(dict(session_id=str(setup.operation_id), preparations=items,
            next_after=None if page.next_after is None else str(page.next_after)), sort_keys=True), flush=True)
        return None
    after = None
    preparation = None
    while True:
        page = await context.preparations(setup, after=after)
        preparation = next((item for item in page.preparations if item.id == args.assignment_id), None)
        if preparation is not None or page.next_after is None:
            break
        after = page.next_after
    if preparation is None:
        raise OnboardingClientError("onboarding_mcp_preparation_unavailable")
    if preparation.proposal.account_type != "mcp_server" or preparation.proposal.packaging != "bridge":
        raise OnboardingClientError("onboarding_mcp_bridge_required")
    target = selected_target(args)
    inputs = target_inputs(target, credential_environment_names=credential_mapping(args.credential_env),
        http_token_environment=args.http_token_env, http_auth_header=args.http_auth_header)
    receipt = await context.pickup_preparation(setup, preparation, inputs=inputs)
    if interaction is None:
        print(f"Prepared MCP connection requested: {receipt.id}. Administrator approval is separate.", flush=True)
    else:
        interaction.emit("mcp_request_submitted", request_id=str(receipt.id))
    return receipt.id
