# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public identity-mode nouns for callable-agent integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from atellagent_client.sdk.config_models import ServiceAccountConfig

BOUNDARY_IDENTITY_ONLY = "boundary_identity_only"
FEDERATED_AGENT_IDENTITY = "federated_agent_identity"
EXTERNAL_AGENT_IDENTITY_MODES = (
    BOUNDARY_IDENTITY_ONLY,
    FEDERATED_AGENT_IDENTITY,
)

ExternalAgentIdentityMode = Literal[
    "boundary_identity_only",
    "federated_agent_identity",
]


def normalize_identity_mode(
    value: Any,
    *,
    default: ExternalAgentIdentityMode = BOUNDARY_IDENTITY_ONLY,
) -> ExternalAgentIdentityMode:
    candidate = str(value or default).strip().lower()
    if candidate not in EXTERNAL_AGENT_IDENTITY_MODES:
        allowed = "', '".join(EXTERNAL_AGENT_IDENTITY_MODES)
        raise ValueError(f"identity_mode must be one of '{allowed}'")
    return candidate  # type: ignore[return-value]


@dataclass(frozen=True)
class IdentityModeSemantics:
    mode: ExternalAgentIdentityMode
    policy_subject: str
    audit_actor: str
    external_principal_required: bool
    boundary_binding_required: bool
    summary: str


_IDENTITY_MODE_SEMANTICS = {
    BOUNDARY_IDENTITY_ONLY: IdentityModeSemantics(
        mode=BOUNDARY_IDENTITY_ONLY,
        policy_subject="service_account",
        audit_actor="execution boundary",
        external_principal_required=False,
        boundary_binding_required=False,
        summary=(
            "Actions are governed and attributed to the enrolled Atellagent "
            "service-account boundary."
        ),
    ),
    FEDERATED_AGENT_IDENTITY: IdentityModeSemantics(
        mode=FEDERATED_AGENT_IDENTITY,
        policy_subject="agent_principal + bound service_account",
        audit_actor="federated external agent",
        external_principal_required=True,
        boundary_binding_required=True,
        summary=(
            "Actions are governed against the resolved external agent principal "
            "while retaining the enrolled boundary as the execution path."
        ),
    ),
}


def get_identity_mode_semantics(
    mode: Any,
) -> IdentityModeSemantics:
    return _IDENTITY_MODE_SEMANTICS[normalize_identity_mode(mode)]


def resolve_identity_mode_from_config(
    config: ServiceAccountConfig,
) -> ExternalAgentIdentityMode:
    # Runtime-local deployment configuration is never identity authority. A
    # caller that operates in federated mode must receive that typed decision
    # from its provisioned control-plane contract and pass it explicitly.
    del config
    return BOUNDARY_IDENTITY_ONLY


__all__ = [
    "BOUNDARY_IDENTITY_ONLY",
    "FEDERATED_AGENT_IDENTITY",
    "ExternalAgentIdentityMode",
    "IdentityModeSemantics",
    "get_identity_mode_semantics",
    "normalize_identity_mode",
    "resolve_identity_mode_from_config",
]
