"""Retained MCP key reservations, without exposing local key material."""

from .errors import OnboardingClientError
from .workload_inventory import identifiers


def bootstrap_page(context, *, after=None):
    linked = {}
    cursor = None
    while True:
        requests, cursor = identifiers(context.drafts, after=cursor)
        for request in requests:
            draft = context.flow(request).draft
            session = draft.setup.operation_id
            if session in linked:
                raise OnboardingClientError("onboarding_mcp_setup_intent_changed")
            linked[session] = request
        if cursor is None:
            break
    sessions, cursor = identifiers(context.setups, after=after)
    rows = []
    for session in sessions:
        setup = context.load_bootstrap(session)
        request = linked.get(session)
        rows.append(dict(session_id=str(session), display_name=setup.display_name,
                         request_id=None if request is None else str(request)))
    return dict(setups=rows, next_after=None if cursor is None else str(cursor))
