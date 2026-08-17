# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Static release-contract checks for the portable client image."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import unittest


class ContainerReleaseContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.dockerfile = (cls.root / "docker/Dockerfile").read_text(encoding="utf-8")
        cls.dockerignore = (cls.root / ".dockerignore").read_text(encoding="utf-8")
        cls.workflow = (cls.root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        cls.lock = (cls.root / "requirements/container-core-py311.lock").read_text(
            encoding="utf-8"
        )
        cls.vex = json.loads(
            (cls.root / "security/vex/python-3.11.16.openvex.json").read_text(
                encoding="utf-8"
            )
        )

    def test_artifact_first_dockerfile_is_pinned_and_core_only(self) -> None:
        self.assertFalse((self.root / "docker/Dockerfile.base").exists())
        self.assertRegex(
            self.dockerfile,
            r"FROM python:3\.11\.16-slim-bookworm@sha256:[0-9a-f]{64}",
        )
        self.assertIn("COPY dist/atellagent_client-*.whl /tmp/", self.dockerfile)
        self.assertIn("COPY requirements/container-core-py311.lock", self.dockerfile)
        self.assertNotIn("COPY . ", self.dockerfile)
        self.assertNotIn("apt-get", self.dockerfile)
        self.assertIn("--require-hashes", self.dockerfile)
        self.assertIn("--only-binary=:all:", self.dockerfile)
        self.assertIn("pip check", self.dockerfile)
        self.assertIn("site-packages/setuptools", self.dockerfile)
        self.assertIn("site-packages/pip", self.dockerfile)
        self.assertIn("site-packages/wheel", self.dockerfile)
        self.assertIn("io.atellagent.client-wheel-sha256", self.dockerfile)
        self.assertNotIn("EXPOSE", self.dockerfile)
        self.assertIn("USER 10001:10001", self.dockerfile)
        self.assertIn("ATELLAGENT_HOOK_SOCKET", self.dockerfile)
        self.assertIn('ENTRYPOINT ["atellagent-cli"]', self.dockerfile)

    def test_docker_build_context_is_a_positive_allowlist(self) -> None:
        lines = {
            line.strip()
            for line in self.dockerignore.splitlines()
            if line.strip() and not line.startswith("#")
        }
        self.assertIn("**", lines)
        self.assertEqual(
            {line for line in lines if line.startswith("!")},
            {
                "!docker/",
                "!docker/Dockerfile",
                "!dist/",
                "!dist/atellagent_client-*.whl",
                "!requirements/",
                "!requirements/container-core-py311.lock",
                "!LICENSE.md",
                "!THIRD_PARTY_NOTICES.md",
            },
        )

    def test_core_lock_is_fully_hashed_and_matches_runtime_dependencies(self) -> None:
        entries = [line for line in self.lock.splitlines() if line and not line.startswith(("#", " "))]
        package_names = {line.split("==", 1)[0].lower() for line in entries}
        self.assertTrue({"httpx", "h2", "pyyaml", "pyjwt", "cryptography"} <= package_names)
        self.assertEqual(len(re.findall(r"--hash=sha256:[0-9a-f]{64}", self.lock)), 18)
        self.assertNotIn("git+", self.lock)
        self.assertNotIn("--extra-index-url", self.lock)

    def test_workflow_uses_immutable_actions_and_only_tag_release_can_publish(self) -> None:
        action_refs = re.findall(r"^\s*- uses: ([^\s]+)@([^\s#]+)", self.workflow, re.MULTILINE)
        self.assertGreaterEqual(len(action_refs), 10)
        for action, ref in action_refs:
            self.assertRegex(ref, r"^[0-9a-f]{40}$", f"{action} is not SHA-pinned")
        self.assertIn("client-release:", self.workflow)
        self.assertIn("environment: client-release", self.workflow)
        self.assertIn("github.event_name == 'push' && github.ref_type == 'tag'", self.workflow)
        self.assertIn("packages: write", self.workflow)
        self.assertIn("attestations: write", self.workflow)
        self.assertIn("id-token: write", self.workflow)
        self.assertIn("linux/amd64,linux/arm64", self.workflow)
        self.assertIn("--provenance=mode=max", self.workflow)
        self.assertIn("--sbom=true", self.workflow)
        self.assertIn("exact image version already exists", self.workflow)
        self.assertIn("severity-cutoff: high", self.workflow)
        self.assertIn("fail-build: true", self.workflow)
        self.assertIn("only-fixed: true", self.workflow)
        self.assertIn("vex: security/vex/python-3.11.16.openvex.json", self.workflow)

    def test_cp31116_vex_is_narrow_and_tracks_the_fixed_component(self) -> None:
        self.assertEqual(self.vex["@context"], "https://openvex.dev/ns/v0.2.0")
        self.assertEqual(self.vex["version"], 1)
        self.assertEqual(
            {statement["vulnerability"]["name"] for statement in self.vex["statements"]},
            {
                "CVE-2026-7210",
                "CVE-2026-11940",
                "CVE-2026-15308",
                "CVE-2026-6100",
                "CVE-2026-4224",
                "CVE-2026-11972",
                "CVE-2026-3644",
                "CVE-2026-9669",
                "CVE-2026-3298",
                "CVE-2026-4786",
            },
        )
        for statement in self.vex["statements"]:
            self.assertEqual(statement["status"], "fixed")
            product = statement["products"]
            self.assertEqual(product[0]["@id"], "atellagent-client:scan")
            self.assertEqual(
                product[0]["subcomponents"], [{"@id": "pkg:generic/python@3.11.16"}]
            )

    def test_public_inventory_and_docker_docs_cover_the_runtime_contract(self) -> None:
        setup_tree = ast.parse((self.root / "setup.py").read_text(encoding="utf-8"))
        source_paths = next(
            ast.literal_eval(node.value)
            for node in setup_tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "PUBLIC_SOURCE_PATHS"
        )
        for path in (
            "docker/Dockerfile",
            "requirements/container-core-py311.lock",
            "tests/test_container_release.py",
        ):
            self.assertIn(path, source_paths)
        docs = (self.root / "docker/README.md").read_text(encoding="utf-8")
        for phrase in (
            "sha256:<manifest-digest>",
            "--enroll",
            "read-only",
            "Derived images",
            "Docker Desktop",
            "client-release",
        ):
            self.assertIn(phrase, docs)


if __name__ == "__main__":
    unittest.main()
