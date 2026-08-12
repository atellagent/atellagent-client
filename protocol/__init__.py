# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Versioned public transport and portable-context contracts."""

from .api import (
    CLIENT_LIBRARY_VERSION,
    DEFAULT_API_VERSION,
    DEFAULT_CONTRACT_VERSION,
    build_client_compat_headers,
    build_versioned_route,
    normalize_api_version,
    normalize_contract_version,
    strip_api_suffix,
)
from .agents import (
    AgentTurnRequest,
    ExternalIdentityEvidence,
    GovernanceCallContext,
    GovernanceReceipt,
    GuardrailDecision,
    RuntimeIngressEnvelope,
    ToolCallRequest,
)
from .context import (
    apply_workflow_headers,
    extract_portable_workflow_context,
    get_workflow_context,
    merge_portable_workflow_context,
    normalize_portable_workflow_context,
    reset_workflow_context,
    serialize_portable_workflow_context,
    set_workflow_context,
)

__all__ = [
    "CLIENT_LIBRARY_VERSION",
    "DEFAULT_API_VERSION",
    "DEFAULT_CONTRACT_VERSION",
    "build_client_compat_headers",
    "build_versioned_route",
    "normalize_api_version",
    "normalize_contract_version",
    "strip_api_suffix",
    "AgentTurnRequest",
    "ExternalIdentityEvidence",
    "GovernanceCallContext",
    "GovernanceReceipt",
    "GuardrailDecision",
    "RuntimeIngressEnvelope",
    "ToolCallRequest",
    "apply_workflow_headers",
    "extract_portable_workflow_context",
    "get_workflow_context",
    "merge_portable_workflow_context",
    "normalize_portable_workflow_context",
    "reset_workflow_context",
    "serialize_portable_workflow_context",
    "set_workflow_context",
]
