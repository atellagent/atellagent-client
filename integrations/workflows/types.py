# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Versioned public contracts for customer-operated workflow participants."""

from __future__ import annotations

from typing import Any, Dict, TypedDict


WORKFLOW_PARTICIPANT_PROTOCOL_VERSION = "v1"


class WorkflowParticipantCompileRequest(TypedDict, total=False):
    protocol_version: str
    tenant_id: str
    deployment_id: str
    runtime_type: str
    workflow_definition: Dict[str, Any]
    runtime_configuration: Dict[str, Any]


class WorkflowParticipantExecutionRequest(TypedDict, total=False):
    protocol_version: str
    operation: str
    execution_id: str
    deployment_id: str
    attempt_id: str
    request_id: str
    runtime_input: Dict[str, Any]
    workflow_context: Dict[str, Any]


class WorkflowParticipantResumeRequest(WorkflowParticipantExecutionRequest, total=False):
    continuation: Dict[str, Any]
    wake_input: Dict[str, Any]


class WorkflowParticipantCancelRequest(TypedDict, total=False):
    protocol_version: str
    operation: str
    tenant_id: str
    execution_id: str
    attempt_id: str
    reason: str


WorkflowParticipantResult = Dict[str, Any]


__all__ = [
    "WORKFLOW_PARTICIPANT_PROTOCOL_VERSION",
    "WorkflowParticipantCancelRequest",
    "WorkflowParticipantCompileRequest",
    "WorkflowParticipantExecutionRequest",
    "WorkflowParticipantResult",
    "WorkflowParticipantResumeRequest",
]
