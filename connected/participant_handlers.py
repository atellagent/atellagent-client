# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Operation handler contracts and exact/wildcard registration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Union


from .actions import ConnectedActionClient
from .contracts import (
    ConnectedDelivery,
    ConnectedHandlerResult,
)

ConnectedHandler = Callable[
    [ConnectedDelivery, ConnectedActionClient],
    Union[ConnectedHandlerResult, Awaitable[ConnectedHandlerResult]],
]


@dataclass(frozen=True)
class ConnectedOperationHandler:
    """Handler plus an explicit statement about external-effect retry safety."""

    handler: ConnectedHandler
    consequential: bool
    idempotency_mode: str

    def __post_init__(self) -> None:
        if self.idempotency_mode not in {"none", "target"}:
            raise ValueError("idempotency_mode must be 'none' or 'target'")
        if self.consequential and self.idempotency_mode != "target":
            raise ValueError(
                "consequential handlers must propagate delivery.idempotency_key "
                "to an idempotent target"
            )


class ConnectedHandlerRegistryMixin:
    def _add_handler(
        self, operation: str, registration: ConnectedOperationHandler
    ) -> None:
        normalized = str(operation or "").strip()
        if not normalized or len(normalized) > 64:
            raise ValueError("operation must be between 1 and 64 characters")
        if "*" in normalized and not normalized.endswith(".*"):
            raise ValueError(
                "operation wildcard is supported only as a trailing .* suffix"
            )
        if normalized in self._handlers:
            raise ValueError(f"handler already registered for {normalized}")
        self._handlers[normalized] = registration

    def register_handler(
        self,
        operation: str,
        handler: ConnectedHandler,
        *,
        consequential: bool,
        idempotency_mode: str = "none",
    ) -> None:
        self._add_handler(
            operation,
            ConnectedOperationHandler(
                handler=handler,
                consequential=bool(consequential),
                idempotency_mode=idempotency_mode,
            ),
        )

    def _handler_for_operation(
        self, operation: str
    ) -> Optional[ConnectedOperationHandler]:
        exact = self._handlers.get(operation)
        if exact is not None:
            return exact
        matches = [
            (pattern[:-1], registration)
            for pattern, registration in self._handlers.items()
            if pattern.endswith(".*") and operation.startswith(pattern[:-1])
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: len(item[0]), reverse=True)
        return matches[0][1]
