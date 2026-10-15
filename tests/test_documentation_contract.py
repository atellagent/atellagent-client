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
                "OpenAIResponsesFacadeRuntime",
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

    def test_public_guides_exclude_local_or_managed_implementation_detail(self) -> None:
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

    def test_public_docs_are_in_the_source_inventory(self) -> None:
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

    def test_checkout_fixtures_are_not_imported_from_the_installed_package(self) -> None:
        for source in (self.root / "tests").rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    self.assertFalse(
                        name == "atellagent_client.tests" or name.startswith("atellagent_client.tests."),
                        f"{source.relative_to(self.root)} imports a fixture excluded from the installed package",
                    )


if __name__ == "__main__":
    unittest.main()
