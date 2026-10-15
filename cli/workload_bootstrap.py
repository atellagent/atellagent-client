"""CLI presentation for shared workload bootstrap and saved submission."""

from atellagent_client.sdk import SetupManagementClient
from .setup_login import login, select_membership
from atellagent_client.onboarding.bootstrap_origin import bootstrap_origin
from atellagent_client.onboarding.setup_keys import _plain_text


async def bootstrap_workload(context, args):
    _plain_text(args.name, 128)
    bootstrap_origin(args.platform_url)
    tokens = await login(args.platform_url)
    async with SetupManagementClient(origin=args.platform_url, tokens=tokens) as management:
        operator, tenant = await select_membership(management)
        setup = await context.prepare_bootstrap(management, name=args.name,
                                                tenant_id=tenant, owner_user_id=operator)
        print(f"Workload bootstrap saved: {setup.operation_id}", flush=True)
        receipt = await context.submit_bootstrap(management, setup.operation_id)
    print(f"Workload bootstrap {receipt.status}. No workload has been provisioned.", flush=True)
    return setup


async def submit_bootstrap(context, session_id):
    setup = context.load_bootstrap(session_id)
    tokens = await login(setup.platform_origin)
    async with SetupManagementClient(origin=setup.platform_origin, tokens=tokens) as management:
        receipt = await context.submit_bootstrap(management, session_id)
    print(f"Workload bootstrap {receipt.status}: {setup.operation_id}", flush=True)
