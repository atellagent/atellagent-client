# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Internal request-scoped authority for customer-operated proxy transports.

This module is deliberately not part of the supported SDK or runtime API.  Its
contents remain source-readable in the client distribution; its purpose is to
keep credentials out of customer handler envelopes, public workflow context,
telemetry, and serialized runtime data.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Optional

from atellagent_client.protocol.context import (
    strip_runtime_authority_headers as _strip_runtime_authority_headers,
)


_EXECUTION_HEADER = "X-Workflow-Execution-Token"
_ACTION_OBLIGATION_HEADER = "X-Atellagent-Action-Obligation"


@dataclass(frozen=True)
class RuntimeAuthority:
    """Validated authority retained only for the active proxy request."""

    execution_token: Optional[str] = None


_runtime_authority_var: ContextVar[Optional[RuntimeAuthority]] = ContextVar(
    "atellagent_runtime_authority",
    default=None,
)


def bind_runtime_authority(
    *,
    execution_token: Optional[str],
):
    """Bind already-validated credentials for internal transport use only."""
    return _runtime_authority_var.set(
        RuntimeAuthority(
            execution_token=str(execution_token).strip() if execution_token else None,
        )
    )


def reset_runtime_authority(token: Any) -> None:
    if token is not None:
        _runtime_authority_var.reset(token)


def strip_runtime_authority_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Remove credentials that are never accepted through public headers."""

    return _strip_runtime_authority_headers(headers)


def apply_runtime_authority_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Inject the active validated execution credential into a gateway request.

    Callers cannot supply an execution or action-obligation header through the
    public workflow context.  The active inbound request is the sole source of
    this credential.
    """
    augmented = strip_runtime_authority_headers(headers)
    authority = _runtime_authority_var.get()
    if authority and authority.execution_token:
        augmented[_EXECUTION_HEADER] = authority.execution_token
    return augmented


__all__: list[str] = []
