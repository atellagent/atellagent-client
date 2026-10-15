# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Attended selection before enrolling a discovered coding-host controller."""

from __future__ import annotations

import sys


_NATIVE_HOSTS = frozenset({"codex", "claude-code"})


def confirm_native_host(host: str, *, selected_host: str | None = None) -> None:
    if host not in _NATIVE_HOSTS:
        raise ValueError("Unsupported native coding host")
    print(f"Controller selected from configuration: {host}.")
    print("Discovery does not connect or approve any declared MCP entries.")
    if selected_host is None:
        if not sys.stdin.isatty():
            raise ValueError(
                "Non-interactive enrollment requires --select-host codex or --select-host claude-code"
            )
        print(f"Type {host} to confirm enrollment for this coding host: ", end="", flush=True)
        selected_host = sys.stdin.readline().strip()
    if selected_host != host:
        raise ValueError(
            f"Selected host does not match the approved {host} controller settings"
        )


__all__ = ["confirm_native_host"]
