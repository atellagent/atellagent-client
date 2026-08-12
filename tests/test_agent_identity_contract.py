# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Public contract tests for the versioned nested identity envelope."""

from __future__ import annotations

import unittest

from atellagent_client.protocol.agent_identity import (
    identity_envelope_from_mapping,
    normalize_identity_context,
)


class AgentIdentityContractTests(unittest.TestCase):
    def test_identity_context_uses_one_nested_envelope(self) -> None:
        normalized = normalize_identity_context(
            {
                "tenant_id": "tenant-1",
                "identity_context": {
                    "principal_identity": {
                        "agent_principal_id": "agent-1",
                        "agent_principal_type": "service_account",
                    },
                    "binding_identity": {
                        "binding_id": "binding-1",
                        "binding_type": "agent",
                    },
                },
            }
        )
        self.assertEqual(
            normalized,
            {
                "tenant_id": "tenant-1",
                "identity_context": {
                    "executor_identity": {"service_account_id": None},
                    "principal_identity": {
                        "agent_principal_id": "agent-1",
                        "agent_principal_type": "service_account",
                    },
                    "external_subject_identity": {
                        "identity_provider": None,
                        "external_principal_id": None,
                    },
                    "binding_identity": {
                        "binding_id": "binding-1",
                        "binding_type": "agent",
                    },
                },
            },
        )

    def test_flat_identity_fields_are_not_a_public_input_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "nested identity_context envelope"):
            identity_envelope_from_mapping({"agent_principal_id": "agent-1"})
