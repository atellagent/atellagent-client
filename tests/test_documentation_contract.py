# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Keep public documentation claims aligned with public client contracts."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
import re
import unittest


class DocumentationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.docs = {
            path.relative_to(cls.root).as_posix(): path.read_text(encoding="utf-8")
            for path in cls.root.rglob("*.md")
            if not any(part.startswith(".") for part in path.relative_to(cls.root).parts)
        }

    def test_documentation_map_and_nearby_guides_exist(self) -> None:
        expected = {
            "docs/README.md",
            "connected/README.md",
            "sdk/README.md",
            "governance/README.md",
            "pep/README.md",
            "proxy/README.md",
            "integrations/README.md",
            "integrations/agents/README.md",
            "integrations/providers/README.md",
            "integrations/tools/README.md",
            "integrations/models/README.md",
            "integrations/channels/README.md",
            "integrations/workflows/README.md",
            "docs/hosts/claude-code.md",
            "docs/hosts/claude-code-route-mode.md",
            "docs/hosts/codex.md",
            "docs/hosts/gemini-cli.md",
            "docs/hosts/claude-cowork.md",
        }
        self.assertTrue(expected <= self.docs.keys(), expected - self.docs.keys())
        index = self.docs["docs/README.md"]
        for link in (
            "../sdk/README.md",
            "../connected/README.md",
            "HOST_HOOKS.md",
            "../integrations/providers/README.md",
            "../governance/README.md",
            "../pep/README.md",
            "../proxy/README.md",
            "../integrations/README.md",
            "../docker/README.md",
            "hosts/claude-code.md",
            "hosts/claude-code-route-mode.md",
            "hosts/codex.md",
            "hosts/gemini-cli.md",
            "hosts/claude-cowork.md",
        ):
            self.assertIn(link, index)

    def test_root_start_guide_declares_the_non_overlapping_axes(self) -> None:
        root = self.docs["README.md"]
        self.assertLessEqual(len(root.splitlines()), 150)
        for term in (
            "hosted",
            "connected",
            "external_resource",
            "sdk",
            "bridge",
            "hook",
            "provider_proxy",
            "mcp_proxy",
            "decision",
            "route",
            "boundary_identity_only",
            "federated_agent_identity",
        ):
            self.assertIn(f"`{term}`", root)

    def test_host_coverage_is_precise_and_deferred_hosts_are_not_claimed(self) -> None:
        claude = self.docs["docs/hosts/claude-code.md"]
        codex = self.docs["docs/hosts/codex.md"]
        for page in (claude, codex):
            self.assertIn("`turn_entry`", page)
            self.assertIn("`full_model_request`", page)
            self.assertIn("Not observed", page)
            self.assertIn("Subscription preservation", page)
            self.assertIn("`mcp__atellagent__*`", page)
        self.assertIn("PostToolUseFailure", claude)
        self.assertNotIn("PostToolUseFailure", codex)
        self.assertIn("does not claim Claude Cowork coverage", claude)
        for page in ("docs/hosts/gemini-cli.md", "docs/hosts/claude-cowork.md"):
            self.assertIn("deferred", self.docs[page].lower())
            self.assertIn("does not", self.docs[page].lower())
        route = self.docs["docs/hosts/claude-code-route-mode.md"]
        self.assertIn("`full_model_request`", route)
        self.assertIn("`stream: true`", route)
        self.assertIn("not preserved", route.lower())

    def test_public_guide_links_resolve(self) -> None:
        for relative_path, text in self.docs.items():
            if not (
                relative_path == "README.md"
                or relative_path.startswith(
                    ("docs/", "connected/", "sdk/", "governance/", "pep/", "proxy/", "integrations/", "docker/")
                )
            ):
                continue
            source = self.root / relative_path
            for destination in re.findall(r"\]\(([^)]+)\)", text):
                target = destination.split("#", 1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                self.assertTrue(
                    (source.parent / target).resolve().is_file(),
                    f"{relative_path} links to missing local file {destination}",
                )

    def test_documented_public_entry_points_are_importable(self) -> None:
        for module_name, names in {
            "atellagent_client.connected": ("ConnectedParticipant", "ConnectedBridge"),
            "atellagent_client.sdk": ("AtellagentClient", "ConnectedSDKRuntime"),
            "atellagent_client.governance": ("RuntimeActionGate",),
            "atellagent_client.pep": ("ActionIntent", "evaluate_action"),
            "atellagent_client.proxy": ("MCPAgentProxy", "MCPToolProxy"),
            "atellagent_client.integrations.agents": (
                "AnthropicMessagesFacadeRuntime",
                "HookControlRuntime",
                "host_hook_capabilities",
            ),
            "atellagent_client.integrations.providers": ("GovernedProviderSession", "ModelGovernanceMode"),
            "atellagent_client.integrations.tools": ("PostgresTools",),
            "atellagent_client.integrations.models": ("ModelRuntimeHandler",),
            "atellagent_client.integrations.channels": ("ChannelAdapterRegistry",),
            "atellagent_client.integrations.workflows": ("WorkflowParticipantHandler",),
        }.items():
            module = importlib.import_module(module_name)
            for name in names:
                self.assertTrue(hasattr(module, name), f"{module_name}.{name}")

    def test_public_docs_and_inventory_reject_local_or_managed_detail(self) -> None:
        public_guides = {
            path: text
            for path, text in self.docs.items()
            if path == "README.md"
            or path.startswith(("docs/", "connected/", "sdk/", "governance/", "pep/", "proxy/", "integrations/", "docker/"))
        }
        joined = "\n".join(public_guides.values()).lower()
        for forbidden in (
            "/users/",
            "src/backend",
            "src/gateway",
            "policy.rego",
            "detector prompt",
            "database table",
            "kubectl",
        ):
            self.assertNotIn(forbidden, joined)
        tree = ast.parse((self.root / "setup.py").read_text(encoding="utf-8"))
        source_paths = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "PUBLIC_SOURCE_PATHS"
        )
        for path in self.docs:
            self.assertIn(path, source_paths, path)


if __name__ == "__main__":
    unittest.main()
