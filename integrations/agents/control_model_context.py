# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Portable principal resolution for model decisions and invocations."""

from __future__ import annotations

from typing import Any, Dict, Optional

from atellagent_client.protocol.agent_contracts import (
    ExternalIdentityEvidence,
)

from .identity_mode import FEDERATED_AGENT_IDENTITY


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def resolve_model_workflow_context_sync(
    governance: Any,
    *,
    workflow_context: Optional[Dict[str, Any]],
    identity: Optional[ExternalIdentityEvidence],
) -> Dict[str, Any]:
    explicit_context = _coerce_dict(workflow_context)
    if governance._has_bound_principal_context(explicit_context):
        return governance._merge_context(explicit_context=explicit_context)

    identity_evidence = identity or ExternalIdentityEvidence()
    principal_context: Dict[str, Any] = {}
    has_identity_evidence = bool(
        identity_evidence.bearer_token
        or (
            identity_evidence.identity_provider
            and identity_evidence.external_principal_id
        )
    )
    if (
        governance.identity_mode == FEDERATED_AGENT_IDENTITY
        and not has_identity_evidence
    ):
        raise RuntimeError(
            "federated governed model calls require external agent identity "
            "evidence or workflow_context with a bound principal"
        )
    if governance.identity_mode == FEDERATED_AGENT_IDENTITY or has_identity_evidence:
        bootstrap = governance.bootstrap_sync(identity_evidence)
        principal_context = bootstrap.principal_context

    resolved = governance._merge_context(
        explicit_context=explicit_context,
        principal_context=principal_context,
    )
    if not governance._has_bound_principal_context(resolved):
        raise RuntimeError(
            "governed model calls require a resolved agent principal binding; "
            "provide federated identity or workflow_context with bound principal fields"
        )
    return resolved


async def resolve_model_workflow_context_async(
    governance: Any,
    *,
    workflow_context: Optional[Dict[str, Any]],
    identity: Optional[ExternalIdentityEvidence],
) -> Dict[str, Any]:
    explicit_context = _coerce_dict(workflow_context)
    if governance._has_bound_principal_context(explicit_context):
        return governance._merge_context(explicit_context=explicit_context)

    identity_evidence = identity or ExternalIdentityEvidence()
    principal_context: Dict[str, Any] = {}
    has_identity_evidence = bool(
        identity_evidence.bearer_token
        or (
            identity_evidence.identity_provider
            and identity_evidence.external_principal_id
        )
    )
    if (
        governance.identity_mode == FEDERATED_AGENT_IDENTITY
        and not has_identity_evidence
    ):
        raise RuntimeError(
            "federated governed model calls require external agent identity "
            "evidence or workflow_context with a bound principal"
        )
    if governance.identity_mode == FEDERATED_AGENT_IDENTITY or has_identity_evidence:
        bootstrap = await governance.bootstrap_async(identity_evidence)
        principal_context = bootstrap.principal_context

    resolved = governance._merge_context(
        explicit_context=explicit_context,
        principal_context=principal_context,
    )
    if not governance._has_bound_principal_context(resolved):
        raise RuntimeError(
            "governed model calls require a resolved agent principal binding; "
            "provide federated identity or workflow_context with bound principal fields"
        )
    return resolved


def resolve_model_decision_context_sync(
    governance: Any,
    *,
    workflow_context: Optional[Dict[str, Any]],
    identity: Optional[ExternalIdentityEvidence],
) -> Dict[str, Any]:
    """Resolve only portable identity facts needed for a decision request."""
    explicit_context = _coerce_dict(workflow_context)
    if governance.identity_mode != FEDERATED_AGENT_IDENTITY:
        return governance._merge_context(explicit_context=explicit_context)
    if governance._has_bound_principal_context(explicit_context):
        return governance._merge_context(explicit_context=explicit_context)
    evidence = identity or ExternalIdentityEvidence()
    if not evidence.bearer_token:
        raise RuntimeError(
            "federated model decisions require trusted identity evidence"
        )
    bootstrap = governance.bootstrap_sync(evidence)
    return governance._merge_context(
        explicit_context=explicit_context,
        principal_context=bootstrap.principal_context,
    )


async def resolve_model_decision_context_async(
    governance: Any,
    *,
    workflow_context: Optional[Dict[str, Any]],
    identity: Optional[ExternalIdentityEvidence],
) -> Dict[str, Any]:
    explicit_context = _coerce_dict(workflow_context)
    if governance.identity_mode != FEDERATED_AGENT_IDENTITY:
        return governance._merge_context(explicit_context=explicit_context)
    if governance._has_bound_principal_context(explicit_context):
        return governance._merge_context(explicit_context=explicit_context)
    evidence = identity or ExternalIdentityEvidence()
    if not evidence.bearer_token:
        raise RuntimeError(
            "federated model decisions require trusted identity evidence"
        )
    bootstrap = await governance.bootstrap_async(evidence)
    return governance._merge_context(
        explicit_context=explicit_context,
        principal_context=bootstrap.principal_context,
    )
