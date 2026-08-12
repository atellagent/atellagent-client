# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Portable contracts for customer-operated connected agent handlers."""

from .boundary_contract import (
    BoundaryExecutionMetadata,
    BoundaryExecutionOutcome,
    BoundaryToolCall,
    extract_boundary_tool_call,
    normalize_boundary_execution_outcome,
)
from .capabilities import (
    ProviderCapabilitySet,
    RuntimeWaitCapabilitySet,
    runtime_wait_capability_set,
)

__all__ = [
    "BoundaryExecutionMetadata",
    "BoundaryExecutionOutcome",
    "BoundaryToolCall",
    "ProviderCapabilitySet",
    "RuntimeWaitCapabilitySet",
    "extract_boundary_tool_call",
    "normalize_boundary_execution_outcome",
    "runtime_wait_capability_set",
]
