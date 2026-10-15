"""Resolve a selected discovered or manually entered target without contacting it."""

from pathlib import Path


def choice_target(item):
    if set(item) == {"name", "host", "discovered_name"}:
        from atellagent_client.discovery.targets import select_target
        return select_target(host=item["host"], name=item["discovered_name"], home=Path.home())
    from .mcp_manual_target import manual_target
    return manual_target(url=item["url"] or None, command=item["command"] or None,
                         arguments=item["arguments"])
