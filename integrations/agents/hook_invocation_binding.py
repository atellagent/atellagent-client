# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.
"""Opaque host-completion binding for the admitted input and explicit rewrite."""

from typing import Any, Mapping
from uuid import UUID
from atellagent_client.pep.contracts import raw_action_fingerprint


def expected_postflight_fingerprint(
    *, tool_name: str, arguments: Mapping[str, Any],
    raw_context: Mapping[str, Any], assessment_reference: str | None = None,
) -> str:
    facts = dict(arguments)
    if assessment_reference is not None and raw_context.get("host") == "codex":
        if not tool_name.startswith("mcp__") or "_atellagent_check" in facts:
            raise ValueError("unsupported assessment-reference rewrite")
        try:
            reference = str(UUID(assessment_reference))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("invalid assessment reference") from exc
        facts["_atellagent_check"] = reference
    return raw_action_fingerprint(action=tool_name, facts=facts, raw_context=raw_context)
