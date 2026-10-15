# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Pinned Gateway fingerprint vectors for native host directives."""

from __future__ import annotations

import unittest

from atellagent_client.pep.contracts import raw_action_fingerprint


class GatewayFingerprintContractTests(unittest.TestCase):
    def test_claude_write_matches_gateway_fingerprint(self) -> None:
        self.assertEqual(
            raw_action_fingerprint(
                action="Write",
                facts={"file_path": "/workspace/é.py", "content": "x"},
                raw_context={
                    "host": "claude-code",
                    "adapter_version": "atellagent.host-hooks.v1",
                },
            ),
            "13e49e9dc2541465ece6eed34978677ea4f74212685c8a7ecd25abbcd49676ff",
        )

    def test_codex_cwd_matches_gateway_and_changes_binding(self) -> None:
        facts = {"path": "/workspace/chart.png"}
        context = {
            "host": "codex",
            "adapter_version": "atellagent.host-hooks.v1",
            "host_cwd": "/workspace",
        }
        expected = "ef18bf2ca23983edd712163dc7978d6b79d1eb1684ff01e31b65fce51d1971e6"
        self.assertEqual(
            raw_action_fingerprint(action="view_image", facts=facts, raw_context=context),
            expected,
        )
        self.assertNotEqual(
            raw_action_fingerprint(
                action="view_image", facts=facts,
                raw_context={**context, "host_cwd": "/other"},
            ),
            expected,
        )


if __name__ == "__main__":
    unittest.main()
