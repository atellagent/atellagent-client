# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bind signature-verified gateway claims to one local PEP action."""

from __future__ import annotations

from time import time
from typing import Any, Callable, Dict, Optional
from uuid import UUID

from .contracts import (
    ActionIntent, DIRECTIVE_VERSION, DirectiveValidationError, RemoteControlDirective,
    _mode, _strings, _text,
)


def _repository_release_claim(value: Any) -> None:
    """Validate the Gateway's bounded state-version claim when it is present."""
    if not isinstance(value, dict) or set(value) != {
        "repository_binding_id", "binding_revision", "status", "state_revision"
    }:
        raise DirectiveValidationError("remote_directive_invalid")
    try:
        repository_id = UUID(value["repository_binding_id"])
    except (TypeError, ValueError, AttributeError) as exc:
        raise DirectiveValidationError("remote_directive_invalid") from exc
    if (
        str(repository_id) != value["repository_binding_id"]
        or type(value["binding_revision"]) is not int
        or value["binding_revision"] < 1
        or type(value["state_revision"]) is not int
        or value["state_revision"] < 1
        or value["status"] not in {"draft", "approved", "revoked"}
    ):
        raise DirectiveValidationError("remote_directive_invalid")


def directive_from_verified_claims(
    claims: Dict[str, Any],
    intent: ActionIntent,
    *,
    audience: str,
    issuer: str = "gateway",
    is_revoked: Optional[Callable[[str], bool]] = None,
    seen: Optional[set[str]] = None,
    now: Callable[[], float] = time,
) -> RemoteControlDirective:
    """Bind already signature-validated claims to one local action intent.

    This is used by the gateway/JWKS verifier after cryptographic validation.
    It deliberately performs the action and portable-scope checks separately,
    so another signed gateway capability cannot be substituted for this action.
    """
    if not isinstance(claims, dict):
        raise DirectiveValidationError("remote_directive_invalid")
    expected_issuer = _text(issuer, "directive issuer", required=True)
    if claims.get("iss") != expected_issuer:
        raise DirectiveValidationError("remote_directive_invalid")
    audiences = claims.get("aud")
    if isinstance(audiences, str):
        audiences = [audiences]
    if not isinstance(audiences, list) or audience not in audiences:
        raise DirectiveValidationError("remote_directive_invalid")
    if (
        claims.get("schema_version") != DIRECTIVE_VERSION
        or claims.get("typ") != "atellagent_control_directive"
    ):
        raise DirectiveValidationError("remote_directive_unsupported")
    directive_id = _text(claims.get("jti"), "directive jti", required=True)
    assert directive_id is not None
    try:
        expires_at = int(claims["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DirectiveValidationError("remote_directive_invalid") from exc
    if expires_at <= int(now()):
        raise DirectiveValidationError("remote_directive_stale")
    if not isinstance(claims.get("allowed"), bool):
        raise DirectiveValidationError("remote_directive_invalid")
    revoked = is_revoked or (lambda _directive_id: False)
    seen_directives = seen if seen is not None else set()
    if revoked(directive_id):
        raise DirectiveValidationError("remote_directive_revoked")
    if directive_id in seen_directives:
        raise DirectiveValidationError("remote_directive_replayed")
    if (
        _text(claims.get("action"), "directive action", required=True)
        != intent.action
        or claims.get("integration_type") != intent.capability.integration_type
        or _text(claims.get("correlation_id"), "directive correlation_id", required=True)
        != intent.correlation_id
    ):
        raise DirectiveValidationError("remote_directive_scope_mismatch")
    if claims.get("raw_action_fingerprint") != intent.raw_action_fingerprint:
        raise DirectiveValidationError("remote_directive_scope_mismatch")
    if "repository_release" in claims:
        _repository_release_claim(claims["repository_release"])
    if (
        _text(claims.get("policy_decision_id"), "directive policy_decision_id", required=True)
        != intent.policy_decision_id
    ):
        raise DirectiveValidationError("remote_directive_scope_mismatch")
    descriptor = claims.get("descriptor")
    if not isinstance(descriptor, dict) or set(descriptor) != {
        "id",
        "revision",
        "digest",
        "action_id",
        "execution_route",
        "integration_id",
    }:
        raise DirectiveValidationError("remote_directive_invalid")
    _text(descriptor.get("id"), "directive descriptor.id", required=True)
    descriptor_digest = _text(
        descriptor.get("digest"), "directive descriptor.digest", required=True
    )
    _text(
        descriptor.get("action_id"), "directive descriptor.action_id", required=True
    )
    _text(
        descriptor.get("execution_route"), "directive descriptor.execution_route", required=True
    )
    _text(
        descriptor.get("integration_id"), "directive descriptor.integration_id", required=True
    )
    if (
        not isinstance(descriptor.get("revision"), int)
        or isinstance(descriptor.get("revision"), bool)
        or descriptor["revision"] < 1
        or len(descriptor_digest or "") != 64
        or any(character not in "0123456789abcdef" for character in descriptor_digest or "")
    ):
        raise DirectiveValidationError("remote_directive_invalid")
    for attr in ("tenant_id", "execution_id", "workspace_id"):
        # A missing signed scope must not widen an already scoped local intent.
        # Optional scopes remain absent only when both sides are absent.
        if claims.get(attr) != getattr(intent, attr):
            raise DirectiveValidationError("remote_directive_scope_mismatch")
    seen_directives.add(directive_id)
    return RemoteControlDirective(
        mode=_mode(claims.get("mode")),
        allowed=bool(claims.get("allowed")),
        action=intent.action,
        integration_type=intent.capability.integration_type,
        audience=audience,
        expires_at=expires_at,
        directive_id=directive_id,
        tenant_id=intent.tenant_id,
        execution_id=intent.execution_id,
        workspace_id=intent.workspace_id,
        evidence_requirements=_strings(
            claims.get("evidence_requirements"), "evidence_requirements"
        ),
        reason_code=_text(claims.get("reason_code"), "reason_code"),
    )
