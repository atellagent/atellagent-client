# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Packaging metadata for Atellagent Client Library."""

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist
import os
import shutil

PACKAGE_ROOT = os.path.abspath(os.path.dirname(__file__))

# ``setup.py`` is intentionally executable from the repository root by CI and
# release automation. Anchor setuptools discovery and manifest handling to the
# public package directory so sibling directories can never be discovered or
# included in release artifacts.
os.chdir(PACKAGE_ROOT)

version_path = os.path.join(PACKAGE_ROOT, "_version.py")
version_namespace = {}
with open(version_path, "r", encoding="utf-8") as fh:
    exec(fh.read(), version_namespace)

# Read README for long description
readme_path = os.path.join(PACKAGE_ROOT, "README.md")
with open(readme_path, "r", encoding="utf-8") as fh:
    long_description = fh.read()

# The client repository is also the package root.  Public packages are listed
# explicitly: adding a source directory must never silently make it into a
# wheel. This list is deliberately revised alongside each clean topology
# cutover; only the explicit public packages are candidates.
PUBLIC_PACKAGES = (
    "atellagent_client",
    "atellagent_client.cli",
    "atellagent_client.examples",
    "atellagent_client.examples.agent",
    "atellagent_client.examples.bridge",
    "atellagent_client.examples.channel",
    "atellagent_client.examples.config",
    "atellagent_client.examples.filter",
    "atellagent_client.examples.mcp",
    "atellagent_client.examples.mcp.bridge",
    "atellagent_client.examples.model",
    "atellagent_client.examples.workflow",
    "atellagent_client.sdk.gateway",
    "atellagent_client.pep",
    "atellagent_client.protocol",
    "atellagent_client.proxy",
    "atellagent_client.connected",
    "atellagent_client.governance",
    "atellagent_client.integrations",
    "atellagent_client.integrations.agents",
    "atellagent_client.integrations.channels",
    "atellagent_client.integrations.models",
    "atellagent_client.integrations.providers",
    "atellagent_client.integrations.tools",
    "atellagent_client.integrations.workflows",
    "atellagent_client.sdk",
    "atellagent_client.sdk.client_modules",
    "atellagent_client.sdk.operations_modules",
)

# A clean public repository is a positive export as well. New top-level files
# or directories must be reviewed before they are committed or copied into the
# source-available repository.
PUBLIC_REPOSITORY_ENTRIES = (
    ".dockerignore",
    ".github",
    ".gitignore",
    "CHANGELOG.md",
    "COMPATIBILITY.md",
    "CONTRIBUTING.md",
    "LICENSE.md",
    "MANIFEST.in",
    "README.md",
    "SECURITY.md",
    "SUPPORT.md",
    "THIRD_PARTY_NOTICES.md",
    "__init__.py",
    "_version.py",
    "cli",
    "connected",
    "docs",
    "docker",
    "examples",
    "governance",
    "integrations",
    "pep",
    "protocol",
    "proxy",
    "py.typed",
    "pyproject.toml",
    "requirements",
    "sdk",
    "setup.py",
    "tests",
)

# The source-export gate compares the checked-out repository to this complete
# file-level inventory. It names only approved release paths; adding any file
# requires an explicit release-surface decision.
PUBLIC_SOURCE_PATHS = (
    ".dockerignore",
    ".github/workflows/ci.yml",
    ".gitignore",
    "CHANGELOG.md",
    "COMPATIBILITY.md",
    "CONTRIBUTING.md",
    "LICENSE.md",
    "MANIFEST.in",
    "README.md",
    "SECURITY.md",
    "SUPPORT.md",
    "THIRD_PARTY_NOTICES.md",
    "__init__.py",
    "_version.py",
    "cli/__init__.py",
    "cli/main.py",
    "connected/__init__.py",
    "connected/README.md",
    "connected/actions.py",
    "connected/adapters.py",
    "connected/bridge.py",
    "connected/capability.py",
    "connected/contracts.py",
    "connected/mcp_client.py",
    "connected/participant.py",
    "docs/BUILDING_CUSTOM_INTEGRATIONS.md",
    "docs/HOST_HOOKS.md",
    "docs/README.md",
    "docs/hosts/claude-code.md",
    "docs/hosts/claude-code-route-mode.md",
    "docs/hosts/codex-route-mode.md",
    "docs/hosts/claude-cowork.md",
    "docs/hosts/codex.md",
    "docs/hosts/gemini-cli.md",
    "docker/Dockerfile",
    "docker/README.md",
    "examples/__init__.py",
    "examples/_resources.py",
    "examples/agent/__init__.py",
    "examples/agent/bridge_adapter_quickstart.py",
    "examples/agent/sdk_callable_agent_quickstart.py",
    "examples/bridge/__init__.py",
    "examples/bridge/grpc_bridge_template.py",
    "examples/bridge/queue_bridge_template.py",
    "examples/channel/__init__.py",
    "examples/channel/slack_channel_runtime_quickstart.py",
    "examples/config/__init__.py",
    "examples/config/agent.yaml",
    "examples/config/agent_bridge.yaml",
    "examples/config/channel.yaml",
    "examples/config/filter.yaml",
    "examples/config/gemini-cli-hooks.user.json",
    "examples/config/local_guardrails.yaml",
    "examples/config/mcp.yaml",
    "examples/config/model.yaml",
    "examples/config/workflow.yaml",
    "examples/config/claude-code-hooks.managed.json",
    "examples/config/claude-code-hooks.user.json",
    "examples/config/codex-hooks.managed.toml",
    "examples/config/codex-hooks.user.toml",
    "examples/config/codex-requirements-hooks.toml",
    "examples/config/host-hook-capabilities.json",
    "examples/mcp/__init__.py",
    "examples/mcp/bridge/__init__.py",
    "examples/mcp/bridge/mcp_bridge_quickstart.py",
    "examples/filter/__init__.py",
    "examples/filter/huggingface_text_filter_quickstart.py",
    "examples/model/__init__.py",
    "examples/model/ollama_model_runtime_quickstart.py",
    "examples/workflow/__init__.py",
    "examples/workflow/langgraph_workflow_participant_quickstart.py",
    "governance/__init__.py",
    "governance/README.md",
    "governance/actions.py",
    "integrations/__init__.py",
    "integrations/README.md",
    "integrations/agents/__init__.py",
    "integrations/agents/README.md",
    "integrations/agents/anthropic_facade.py",
    "integrations/agents/openai_facade.py",
    "integrations/agents/boundary_contract.py",
    "integrations/agents/capabilities.py",
    "integrations/agents/contracts.py",
    "integrations/agents/control.py",
    "integrations/agents/control_actions.py",
    "integrations/agents/control_model_invocation.py",
    "integrations/agents/identity_mode.py",
    "integrations/agents/hook_control.py",
    "integrations/agents/host_hooks.py",
    "integrations/channels/__init__.py",
    "integrations/channels/README.md",
    "integrations/channels/contracts.py",
    "integrations/channels/registry.py",
    "integrations/channels/slack.py",
    "integrations/models/__init__.py",
    "integrations/models/README.md",
    "integrations/models/contracts.py",
    "integrations/models/huggingface.py",
    "integrations/models/ollama.py",
    "integrations/providers/__init__.py",
    "integrations/providers/README.md",
    "integrations/providers/anthropic.py",
    "integrations/providers/google.py",
    "integrations/providers/governed_tools.py",
    "integrations/providers/openai.py",
    "integrations/providers/session.py",
    "integrations/tools/__init__.py",
    "integrations/tools/README.md",
    "integrations/tools/postgres.py",
    "integrations/workflows/__init__.py",
    "integrations/workflows/README.md",
    "integrations/workflows/connected_actions.py",
    "integrations/workflows/contracts.py",
    "integrations/workflows/handlers.py",
    "integrations/workflows/langgraph.py",
    "integrations/workflows/types.py",
    "pep/__init__.py",
    "pep/README.md",
    "pep/contracts.py",
    "pep/gateway.py",
    "protocol/__init__.py",
    "protocol/agent_contracts.py",
    "protocol/context.py",
    "protocol/agent_identity.py",
    "protocol/agent_ingress.py",
    "protocol/agent_waits.py",
    "protocol/agents.py",
    "protocol/api.py",
    "protocol/runtime_modes.py",
    "protocol/workflow_waits.py",
    "proxy/__init__.py",
    "proxy/README.md",
    "proxy/agent.py",
    "proxy/cli.py",
    "proxy/contracts.py",
    "proxy/tool.py",
    "py.typed",
    "pyproject.toml",
    "requirements/constraints-py311.lock",
    "requirements/container-core-py311.lock",
    "sdk/__init__.py",
    "sdk/README.md",
    "sdk/auth.py",
    "sdk/client.py",
    "sdk/client_modules/__init__.py",
    "sdk/client_modules/agent_events.py",
    "sdk/client_modules/base.py",
    "sdk/client_modules/client_class.py",
    "sdk/client_modules/factories.py",
    "sdk/client_modules/init_helpers.py",
    "sdk/client_modules/lifecycle.py",
    "sdk/client_modules/mcp_tools.py",
    "sdk/client_modules/model_invocation_client.py",
    "sdk/client_modules/runtime_authority.py",
    "sdk/config.py",
    "sdk/connected.py",
    "sdk/config_coercion.py",
    "sdk/config_deployment.py",
    "sdk/config_loader.py",
    "sdk/config_models.py",
    "sdk/enrollment.py",
    "sdk/errors.py",
    "sdk/gateway/__init__.py",
    "sdk/gateway/session.py",
    "sdk/http.py",
    "sdk/jwks.py",
    "sdk/operations.py",
    "sdk/operations_modules/__init__.py",
    "sdk/operations_modules/agent_events.py",
    "sdk/operations_modules/channels.py",
    "sdk/operations_modules/common.py",
    "sdk/operations_modules/invocation_errors.py",
    "sdk/operations_modules/mcp.py",
    "sdk/operations_modules/model_invocations.py",
    "sdk/telemetry.py",
    "sdk/tls.py",
    "setup.py",
    "tests/test_governed_provider_tools.py",
    "tests/test_governed_provider_session.py",
    "tests/test_provider_integrations.py",
    "tests/test_model_integrations.py",
    "tests/test_model_decisions.py",
    "tests/test_hook_control.py",
    "tests/test_host_hooks.py",
    "tests/test_container_release.py",
    "tests/test_documentation_contract.py",
    "tests/test_postgres_tools.py",
    "tests/test_slack_channel.py",
    "tests/test_workflow_langgraph.py",
    "tests/test_enrollment.py",
    "tests/test_connected_runtime.py",
    "tests/test_agent_identity_contract.py",
    "tests/test_anthropic_facade.py",
    "tests/test_cli_shutdown.py",
    "tests/test_openai_facade.py",
    "tests/test_mcp_proxy.py",
    "tests/fixtures/modern_mcp_reference_server.py",
)

# Public source may import the standard library, this package, or the direct
# runtime/optional-example dependencies named here. The release gate treats
# every other import root as unapproved.
PUBLIC_THIRD_PARTY_IMPORT_ROOTS = (
    "anthropic",
    "cryptography",
    "google",
    "grpc",
    "httpx",
    "httpx2",
    "h2",
    "ollama",
    "openai",
    "psycopg",
    "langgraph",
    "mcp",
    "jwt",
    "setuptools",
    "transformers",
    "yaml",
)

# Setuptools has useful defaults for package source files, but it also adds
# conventional test files to an sdist. The release is a positive export:
# entries not named here are excluded.
PUBLIC_SDIST_ENTRIES = frozenset(
    {
        "CHANGELOG.md",
        "COMPATIBILITY.md",
        "LICENSE.md",
        "MANIFEST.in",
        "PKG-INFO",
        "README.md",
        "SECURITY.md",
        "SUPPORT.md",
        "THIRD_PARTY_NOTICES.md",
        "__init__.py",
        "_version.py",
        "cli",
        "connected",
        "docs",
        "examples",
        "governance",
        "integrations",
        "pep",
        "protocol",
        "proxy",
        "py.typed",
        "pyproject.toml",
        "requirements",
        "sdk",
        "setup.cfg",
        "setup.py",
    }
)


class PublicClientSdist(_sdist):
    """Build an sdist from the explicit public release allowlist."""

    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        for entry in os.scandir(base_dir):
            if entry.name in PUBLIC_SDIST_ENTRIES:
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)


class PublicClientBuildPy(_build_py):
    """Exclude repository build metadata from the mapped root package."""

    def find_package_modules(self, package, package_dir):
        modules = super().find_package_modules(package, package_dir)
        if package != "atellagent_client":
            return modules
        return [module for module in modules if module[1] != "setup"]


setup(
    name="atellagent-client",
    version=version_namespace["CLIENT_LIBRARY_VERSION"],
    author="Atellagent Team",
    author_email="support@atellagent.com",
    description="Standalone client library for Atellagent",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/atellagent/atellagent-client",
    packages=PUBLIC_PACKAGES,
    cmdclass={"build_py": PublicClientBuildPy, "sdist": PublicClientSdist},
    package_dir={"atellagent_client": "."},
    package_data={
        "atellagent_client": ["py.typed"],
        "atellagent_client.examples": ["config/*.yaml", "config/*.json", "config/*.toml"],
    },
    include_package_data=True,
    license="Proprietary",
    license_files=("LICENSE.md", "THIRD_PARTY_NOTICES.md"),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Internet :: WWW/HTTP",
    ],
    python_requires=">=3.11,<3.12",
    install_requires=[
        # HTTP clients for sync and async
        "httpx==0.28.1",
        "h2==4.3.0",
        "PyYAML==6.0.3",
        "PyJWT==2.10.1",
        "cryptography==50.0.0",
    ],
    entry_points={
        "console_scripts": [
            "atellagent-cli=atellagent_client.cli.main:main",
            "atellagent-agent-proxy=atellagent_client.proxy.cli:agent_main",
            "atellagent-tool-proxy=atellagent_client.proxy.cli:tool_main",
            "atellagent-hook-adapter=atellagent_client.integrations.agents.host_hooks:main",
            "atellagent-anthropic-facade=atellagent_client.integrations.agents.anthropic_facade:main",
            "atellagent-openai-facade=atellagent_client.integrations.agents.openai_facade:main",
        ],
    },
    extras_require={
        # The compatibility executables use only the core HTTP dependency.  The
        # named extras keep their install intent explicit without adding an MCP
        # SDK (and, specifically, no legacy MCP SDK) to ordinary installations.
        "mcp-agent-proxy": [],
        "mcp-tool-proxy": [],
        "mcp": [
            "mcp==2.0.0",
        ],
        "openai": [
            "openai==2.53.0",
        ],
        "google": [
            "google-genai==2.16.0",
        ],
        "anthropic": [
            "anthropic==0.72.0",
        ],
        "langgraph": [
            "langgraph==1.0.2",
        ],
        "ollama": [
            "ollama==0.6.2",
        ],
        "huggingface-filter": [
            "transformers==5.14.1",
        ],
        "postgres": [
            "psycopg[binary]==3.3.4",
        ],
        "test": [
            "mcp==2.0.0",
            "openai==2.53.0",
            "google-genai==2.16.0",
            "anthropic==0.72.0",
            "langgraph==1.0.2",
            "ollama==0.6.2",
            "transformers==5.14.1",
            "psycopg[binary]==3.3.4",
        ],
        "dev": [
            "pytest>=7.0.0",
            "pytest-asyncio>=0.21.0",
            "black>=23.0.0",
            "flake8>=6.0.0",
            "mypy>=1.0.0",
        ],
    },
    keywords="atellagent ai gateway policy enforcement oauth2 langgraph workflows",
    project_urls={
        "Bug Reports": "https://github.com/atellagent/atellagent-client/issues",
        "Source": "https://github.com/atellagent/atellagent-client",
        "Documentation": "https://docs.atellagent.com",
    },
)
