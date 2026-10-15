"""Saved-workload CLI actions do not initiate browser auth or import machine configs."""

from atellagent_client.sdk import WorkloadContext
from .workload_options import options, local_inputs


async def run(arguments):
    args = options(arguments)
    context = WorkloadContext(args.state_directory)
    if args.action in {"bootstrap", "bootstrap-submit"}:
        from .workload_bootstrap import bootstrap_workload, submit_bootstrap
        return (await bootstrap_workload(context, args) if args.action == "bootstrap"
                else await submit_bootstrap(context, args.session_id))
    if args.action in {"preparations", "pickup"}:
        from .workload_preparations import run_preparations
        return await run_preparations(context, args)
    if args.action in {"request", "submit"}:
        from .workload_request import request_workload, submit_workload
        from .workload_progress import wait_for_workload
        flow = (await request_workload(context, args) if args.action == "request"
                else await submit_workload(context, args.request_id))
        return await wait_for_workload(flow, args.wait_seconds, state_directory=args.state_directory)
    flow = context.flow(args.request_id)
    if args.action == "configure":
        context.configure(args.request_id, local_inputs(args, context.proposal(args.request_id)))
        print("Local workload launch settings saved. Approved identity and credentials are unchanged.", flush=True)
        return
    if args.action == "resume":
        from .workload_progress import wait_for_workload
        return await wait_for_workload(flow, args.wait_seconds, state_directory=args.state_directory)
    from .workload_participant import run_participant
    await run_participant(context, args.request_id, verify=args.action == "verify")
