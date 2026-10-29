# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""SDK client implementation module exports with lazy loading for heavy client types."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client_class import AtellagentClient
    from .factories import create_service_account_client


def __getattr__(name: str) -> Any:
    if name == "AtellagentClient":
        from .client_class import AtellagentClient

        return AtellagentClient
    if name == "create_service_account_client":
        from .factories import create_service_account_client

        return create_service_account_client
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AtellagentClient",
    "create_service_account_client",
]
