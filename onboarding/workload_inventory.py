"""Bounded retained workload inventory; local files do not imply server readiness."""

import asyncio
import heapq
import os

from .contract_values import canonical_id
from .errors import OnboardingClientError
from .mcp_runtime_status import MCPRuntimeStatus
from .private_paths import require_private_path, require_safe_ancestry


def identifiers(store, *, after=None):
    require_safe_ancestry(store.directory)
    require_private_path(store.directory, directory=True)
    def candidates():
        with os.scandir(store.directory) as entries:
            for count, entry in enumerate(entries, 1):
                if count > 10000:
                    raise OnboardingClientError("onboarding_saved_inventory_unavailable")
                if entry.name.startswith("."):
                    continue
                if entry.name.endswith(("-local.json", "-receipt.json")):
                    canonical_id(entry.name.split("-local.json")[0].split("-receipt.json")[0])
                    continue
                if not entry.name.endswith(".json"):
                    raise OnboardingClientError("onboarding_saved_inventory_unavailable")
                identifier = canonical_id(entry.name[:-5])
                if after is None or identifier > after:
                    yield identifier
    selected = heapq.nsmallest(9, candidates())
    return selected[:8], selected[7] if len(selected) == 9 else None


async def mcp_page(context, *, after=None):
    ids, cursor = identifiers(context.drafts, after=after)
    selected = context.selections.load()
    observations = MCPRuntimeStatus(context.device_context.installation.directory / "mcp-runtime-status")

    async def current(identifier):
        draft = context.flow(identifier).draft
        if draft.proposal.account_type != "mcp_server":
            raise OnboardingClientError("onboarding_mcp_setup_intent_changed")
        row = dict(request_id=str(identifier), display_name=draft.proposal.display_name,
            server_status=None, server_error=None, service_account_id=None, integration_id=None,
            device_id=str(draft.proposal.device_id), selected=identifier in selected,
            local=observations.load(identifier))
        try:
            receipt = await context.status(identifier)
        except OnboardingClientError as exc:
            row["server_error"] = exc.code  # Never reinterpret an unavailable receipt as no submission.
        else:
            row.update(server_status=receipt.status, service_account_id=str(receipt.service_account_id),
                       integration_id=str(receipt.integration_id))
        return row

    rows = await asyncio.gather(*(current(identifier) for identifier in ids))
    return dict(requests=rows, next_after=None if cursor is None else str(cursor))
