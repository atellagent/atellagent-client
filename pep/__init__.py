# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public, bounded policy-enforcement-point primitives.

The PEP validates static local safeguards and opaque directives supplied by a
remote control service. It does not evaluate managed policy.
"""

from .contracts import (
    ActionIntent,
    ControlSource,
    DecisionEnvelope,
    DecisionObserver,
    DirectiveValidationError,
    IntegrationCapability,
    LocalGuardrailManifest,
    RemoteControlDirective,
    RemoteDirectiveVerifier,
    directive_from_verified_claims,
    evaluate_action,
    evaluate_controlled_action,
    evaluate_connected_action,
    load_local_guardrail_manifest,
)
from .gateway import CONTROL_DIRECTIVE_AUDIENCE, GatewayDirectiveVerifier

__all__ = [
    "ActionIntent",
    "ControlSource",
    "DecisionEnvelope",
    "DecisionObserver",
    "DirectiveValidationError",
    "IntegrationCapability",
    "LocalGuardrailManifest",
    "RemoteControlDirective",
    "RemoteDirectiveVerifier",
    "GatewayDirectiveVerifier",
    "CONTROL_DIRECTIVE_AUDIENCE",
    "directive_from_verified_claims",
    "evaluate_action",
    "evaluate_controlled_action",
    "evaluate_connected_action",
    "load_local_guardrail_manifest",
]
