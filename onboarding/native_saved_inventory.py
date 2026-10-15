"""Bounded exact retained proposals reconciled against live own-device assignments."""

import asyncio
import heapq
import os

from .contract_values import canonical_id
from .device_assignments import DeviceAssignment
from .errors import OnboardingClientError
from .private_paths import require_private_path, require_safe_ancestry


def identifiers(store, *, after=None):
    try:
        require_safe_ancestry(store.directory)
        require_private_path(store.directory, directory=True)
        def candidates():
            with os.scandir(store.directory) as entries:
                for count, entry in enumerate(entries, 1):
                    if count > 10000:
                        raise ValueError("bounded retained inventory required")
                    if entry.name.startswith(".setup-"):
                        continue  # Current atomic commit temporaries are never request records.
                    if not entry.name.endswith(".json"):
                        raise ValueError("unexpected retained record")
                    identifier = canonical_id(entry.name[:-5])
                    if after is None or identifier > after:
                        yield identifier
        selected = heapq.nsmallest(9, candidates())
        return selected[:8], selected[7] if len(selected) == 9 else None
    except (OSError, ValueError, TypeError) as exc:
        raise OnboardingClientError("onboarding_saved_inventory_unavailable") from exc


async def page(context, *, after=None):
    ids, cursor = identifiers(context.drafts, after=after)
    async def current(identifier):
        draft = context.load(identifier)
        row = dict(request_id=str(identifier), display_name=draft.proposal.display_name,
            native_host=draft.proposal.native_host, server_status=None, owner_consented_at=None)
        assignment = await context.client.assignment(identifier)
        if assignment is not None:
            try:
                assignment = DeviceAssignment.from_payload(assignment.payload(), device=draft)
                if assignment.proposal != draft.proposal or assignment.onboarding_session_id != draft.setup_id:
                    raise ValueError("server assignment changed the saved proposal")
            except (ValueError, TypeError, AttributeError, KeyError) as exc:
                raise OnboardingClientError("onboarding_native_setup_intent_changed") from exc
            row.update(server_status=assignment.status,
                owner_consented_at=None if assignment.owner_consented_at is None else assignment.owner_consented_at.isoformat())
        return row
    results = await asyncio.gather(*(current(identifier) for identifier in ids), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return dict(requests=results, next_after=None if cursor is None else str(cursor))
