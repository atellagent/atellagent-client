# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""The public PEP surface keeps local evaluation after module extraction."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from atellagent_client.pep import (
    ActionIntent,
    IntegrationCapability,
    evaluate_action,
    load_local_guardrail_manifest,
)


class LocalGuardrailModuleTests(unittest.TestCase):
    def test_public_loader_and_evaluator_keep_allow_and_deny_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "guardrails.yaml"
            manifest_path.write_text(
                "schema_version: v1\nmode: enforce\nactions:\n"
                f"  file.read:\n    readable_roots:\n      - {root}\n",
                encoding="utf-8",
            )
            manifest = load_local_guardrail_manifest(str(manifest_path))
            capability = IntegrationCapability("tool", True)
            allowed = ActionIntent(
                action="file.read",
                capability=capability,
                correlation_id="allowed",
                facts={"path": str(root / "item"), "access": "read"},
            )
            denied = ActionIntent(
                action="file.read",
                capability=capability,
                correlation_id="denied",
                facts={"path": "/outside/item", "access": "read"},
            )
            self.assertEqual("allow", evaluate_action(allowed, local_manifest=manifest).decision)
            decision = evaluate_action(denied, local_manifest=manifest)
            self.assertEqual("deny", decision.decision)
            self.assertEqual("path_not_readable", decision.reason_code)


if __name__ == "__main__":
    unittest.main()
