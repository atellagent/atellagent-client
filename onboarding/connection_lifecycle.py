"""Provision retained device connections without enabling local execution."""

from uuid import UUID
from pathlib import Path

from .native_setup_context import NativeSetupContext
from .device_mcp_context import DeviceMCPContext
from .native_runtime_selection import NativeRuntimeSelections
from .native_saved_inventory import page as native_page
from .errors import OnboardingClientError


async def reconcile_connections(directory):
    """Advance each exact saved request once; approval never selects a runtime."""
    directory = Path(directory)
    rows = []
    async with await NativeSetupContext.open(directory) as context:
        cursor = None
        selections = NativeRuntimeSelections(directory / "native-runtime-selections")
        while True:
            page = await native_page(context, after=cursor)
            for row in page["requests"]:
                identifier = UUID(row["request_id"])
                selected = selections.load(row["native_host"])
                enabled = selected is not None and selected["request_id"] == identifier
                result = dict(kind="native", request_id=str(identifier), enabled=enabled,
                              stage=row["server_status"] or "unsubmitted", error=None)
                if row["owner_consented_at"] is not None and row["server_status"] in {"approved", "issuing", "issued"}:
                    try:
                        result["stage"] = (await context.flow(context.load(identifier)).advance()).stage
                    except OnboardingClientError as exc:
                        result["error"] = exc.code
                rows.append(result)
            cursor = page["next_after"]
            if cursor is None:
                break
            cursor = UUID(cursor)
    async with await DeviceMCPContext.open(directory) as context:
        cursor = None
        while True:
            page = await context.saved(after=cursor)
            for row in page["requests"]:
                identifier = UUID(row["request_id"])
                result = dict(kind="mcp", request_id=str(identifier), enabled=row["selected"],
                              stage=row["server_status"] or "unsubmitted", error=row["server_error"])
                if row["server_status"] in {"approved", "issuing", "issued"}:
                    try:
                        result["stage"] = (await context.flow(identifier).advance()).stage
                    except OnboardingClientError as exc:
                        result["error"] = exc.code
                rows.append(result)
            cursor = page["next_after"]
            if cursor is None:
                break
            cursor = UUID(cursor)
    return dict(connections=rows)
