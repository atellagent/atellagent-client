# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Delivery-scoped governed actions for connected workflow handlers."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Dict, Mapping, Optional


_CONNECTED_ACTIONS: ContextVar[Optional[Any]] = ContextVar(
    "atellagent_connected_workflow_actions", default=None
)


def _bind_connected_actions(actions: Any) -> Token:
    return _CONNECTED_ACTIONS.set(actions)


def _reset_connected_actions(token: Token) -> None:
    _CONNECTED_ACTIONS.reset(token)


class WorkflowParticipantActions:
    """Use governed actions only during one connected execute/resume delivery."""

    async def invoke_mcp(
        self, *, effect_key: str, request: Mapping[str, Any]
    ) -> Dict[str, Any]:
        actions = _CONNECTED_ACTIONS.get()
        if actions is None:
            raise RuntimeError(
                "workflow participant actions are available only while handling "
                "a connected delivery"
            )
        return await actions.invoke_mcp(effect_key=effect_key, request=request)


__all__ = ["WorkflowParticipantActions"]
