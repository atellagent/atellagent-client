# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Strict gateway response validation shared by participant operations."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Set

from .contracts import ConnectedProtocolError


class ConnectedHTTPError(ConnectedProtocolError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"connected gateway request failed ({status_code}): {detail}")
        self.status_code = int(status_code)
        self.detail = detail


def strict_object(value: Any, expected: Set[str], name: str) -> Dict[str, Any]:
    """Reject malformed gateway documents and undocumented fields."""
    if not isinstance(value, Mapping):
        raise ConnectedProtocolError(f"{name} must be an object")
    payload = dict(value)
    extra = set(payload) - expected
    if extra:
        raise ConnectedProtocolError(
            f"{name} contains unsupported fields: {', '.join(sorted(extra))}"
        )
    return payload


__all__ = ["ConnectedHTTPError", "strict_object"]
