# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public versioned agent runtime participation protocol surface."""

from .agent_contracts import (
    AgentTurnRequest,
    ExternalIdentityEvidence,
    GovernanceCallContext,
    GovernanceReceipt,
    GuardrailDecision,
    ToolCallRequest,
    ToolCallResult,
)
from .agent_ingress import (
    INGRESS_ENVELOPE_SCHEMA_VERSION,
    RuntimeIngressEnvelope,
    build_ingress_envelope,
    ingress_envelope_from_mapping,
    normalize_ingress_context,
    supported_ingress_context_keys,
)
from .agent_identity import (
    IDENTITY_CONTEXT_KEY,
    build_identity_envelope,
    has_bound_principal_identity,
    identity_envelope_from_mapping,
    identity_envelope_to_flat_mapping,
    identity_envelope_violations,
    normalize_identity_context,
)
from .agent_waits import (
    normalize_wait_boundary_type,
    supported_wait_boundary_types,
)
from .runtime_modes import normalize_runtime_mode, supported_runtime_modes

__all__ = [
    "AgentTurnRequest",
    "ExternalIdentityEvidence",
    "GovernanceCallContext",
    "GovernanceReceipt",
    "GuardrailDecision",
    "INGRESS_ENVELOPE_SCHEMA_VERSION",
    "IDENTITY_CONTEXT_KEY",
    "RuntimeIngressEnvelope",
    "ToolCallRequest",
    "ToolCallResult",
    "build_identity_envelope",
    "build_ingress_envelope",
    "has_bound_principal_identity",
    "identity_envelope_from_mapping",
    "identity_envelope_to_flat_mapping",
    "identity_envelope_violations",
    "ingress_envelope_from_mapping",
    "normalize_identity_context",
    "normalize_ingress_context",
    "normalize_runtime_mode",
    "normalize_wait_boundary_type",
    "supported_ingress_context_keys",
    "supported_runtime_modes",
    "supported_wait_boundary_types",
]
