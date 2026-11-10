# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Handler contract for the v1 customer-operated workflow participant."""

from __future__ import annotations

from typing import Protocol

from .types import (
    WorkflowParticipantCancelRequest,
    WorkflowParticipantCompileRequest,
    WorkflowParticipantExecutionRequest,
    WorkflowParticipantResult,
    WorkflowParticipantResumeRequest,
)


class WorkflowParticipantHandler(Protocol):
    async def compile(
        self,
        payload: WorkflowParticipantCompileRequest,
    ) -> WorkflowParticipantResult: ...

    async def execute(
        self,
        payload: WorkflowParticipantExecutionRequest,
    ) -> WorkflowParticipantResult: ...

    async def resume(
        self,
        payload: WorkflowParticipantResumeRequest,
    ) -> WorkflowParticipantResult: ...

    async def cancel(
        self,
        payload: WorkflowParticipantCancelRequest,
    ) -> WorkflowParticipantResult: ...


class WorkflowParticipantHandlerBase:
    """Implement only the participant operations advertised at registration."""

    async def compile(
        self,
        payload: WorkflowParticipantCompileRequest,
    ) -> WorkflowParticipantResult:
        raise NotImplementedError("workflow participant compile is not implemented")

    async def execute(
        self,
        payload: WorkflowParticipantExecutionRequest,
    ) -> WorkflowParticipantResult:
        raise NotImplementedError("workflow participant execute is not implemented")

    async def resume(
        self,
        payload: WorkflowParticipantResumeRequest,
    ) -> WorkflowParticipantResult:
        raise NotImplementedError("workflow participant resume is not implemented")

    async def cancel(
        self,
        payload: WorkflowParticipantCancelRequest,
    ) -> WorkflowParticipantResult:
        raise NotImplementedError("workflow participant cancel is not implemented")


__all__ = ["WorkflowParticipantHandler", "WorkflowParticipantHandlerBase"]
