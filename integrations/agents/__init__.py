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
from .hook_control import (
    HOOK_CONTROL_PROTOCOL,
    HookControlClient,
    HookControlError,
    HookControlRuntime,
)
from .host_hooks import (
    HookAdapterResponse,
    handle_host_hook,
    host_hook_capabilities,
)
from .anthropic_facade import (
    AnthropicFacadeError,
    AnthropicFacadeResponse,
    AnthropicMessagesFacadeRuntime,
    load_route_facade_capability_token,
)
from .openai_facade import (
    OpenAIResponsesFacadeError,
    OpenAIResponsesFacadeResponse,
    OpenAIResponsesFacadeRuntime,
)

__all__ = [
    "BoundaryExecutionMetadata",
    "BoundaryExecutionOutcome",
    "BoundaryToolCall",
    "AnthropicFacadeError",
    "AnthropicFacadeResponse",
    "AnthropicMessagesFacadeRuntime",
    "HOOK_CONTROL_PROTOCOL",
    "HookControlClient",
    "HookControlError",
    "HookControlRuntime",
    "OpenAIResponsesFacadeError",
    "OpenAIResponsesFacadeResponse",
    "OpenAIResponsesFacadeRuntime",
    "HookAdapterResponse",
    "ProviderCapabilitySet",
    "RuntimeWaitCapabilitySet",
    "extract_boundary_tool_call",
    "handle_host_hook",
    "host_hook_capabilities",
    "load_route_facade_capability_token",
    "normalize_boundary_execution_outcome",
    "runtime_wait_capability_set",
]
