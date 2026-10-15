"""Attended workload request: visible browser code, tenant choice and saved continuation."""

from uuid import uuid4
from atellagent_client.sdk import SetupManagementClient, WorkloadProposal
from .setup_login import login, select_membership
from .workload_options import local_inputs


async def request_workload(context, args):
    deadline = args.offline_dispatch_deadline_seconds
    if deadline is None and args.offline_dispatch_mode == "queue": deadline = 300
    proposal = WorkloadProposal(uuid4(), args.name, args.account_type, args.packaging,
        tuple(args.capability), identity_mode=args.identity_mode, control_source=args.control_source,
        local_guardrail_mode=args.local_guardrail_mode, offline_dispatch_mode=args.offline_dispatch_mode,
        offline_dispatch_deadline_seconds=deadline, channel_type=args.channel_type,
        filter_execution_boundary=args.filter_execution_boundary)
    inputs = local_inputs(args, proposal)
    tokens = await login(args.platform_url)
    async with SetupManagementClient(origin=args.platform_url, tokens=tokens) as management:
        operator, tenant = await select_membership(management)
        await context.prepare_request(management, proposal=proposal, inputs=inputs,
                                      tenant_id=tenant, owner_user_id=operator)
        print(f"Workload request saved: {proposal.id}", flush=True)
        await context.submit_request(management, proposal.id)
    return context.flow(proposal.id)


async def submit_workload(context, request_id):
    draft = context.drafts.load(request_id)
    tokens = await login(draft.setup.platform_origin)
    async with SetupManagementClient(origin=draft.setup.platform_origin, tokens=tokens) as management:
        await context.submit_request(management, request_id)
    return context.flow(request_id)
