"""Explicit page review/pickup with no login, key creation or runtime start."""

import json

from atellagent_client.onboarding.errors import OnboardingClientError


async def run_preparations(context, args):
    setup = context.load_bootstrap(args.session_id)
    page = await context.preparations(setup, after=args.after)
    if args.action == "preparations":
        items = []
        for item in page.preparations:
            settings = item.proposal.payload(); settings.pop("id")
            settings.pop("device_id"); settings.pop("device_onboarding_session_id")
            items.append(dict(id=str(item.id), status=item.status, settings=settings,
                settings_sha256=item.settings_sha256, expires_at=item.expires_at.isoformat(),
                proposed_request_id=None if item.proposed_request_id is None else str(item.proposed_request_id)))
        payload = dict(session_id=str(setup.operation_id), preparations=items,
            next_after=None if page.next_after is None else str(page.next_after))
        if args.json: print(json.dumps(payload, ensure_ascii=False), flush=True)
        else:
            for item in items: print(f"{item['id']}: {item['settings']['display_name']} — {item['status']}", flush=True)
            if not items: print("No targeted workload preparations on this page.", flush=True)
            if page.next_after: print(f"Next page: --after {page.next_after}", flush=True)
        return payload
    preparation = next((item for item in page.preparations if item.id == args.assignment_id), None)
    if preparation is None:
        if page.next_after: print(f"Next page: --after {page.next_after}", flush=True)
        raise OnboardingClientError("onboarding_workload_preparation_not_on_page")
    receipt = await context.pickup_preparation(setup, preparation)
    print(f"Workload request {receipt.status}: {receipt.id}. Pickup does not start the workload.", flush=True)
    print("Configure local launch settings, then resume setup after approval.", flush=True)
    return receipt
