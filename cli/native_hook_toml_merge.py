"""Preserve Codex TOML while adding only the selected Unix socket permission."""

from collections.abc import Mapping
from copy import deepcopy
from pathlib import PurePosixPath
import tomllib

import tomlkit
from tomlkit.exceptions import ParseError

from atellagent_client.onboarding.errors import OnboardingClientError

LIMIT = 1024 * 1024


def _invalid():
    raise OnboardingClientError("onboarding_native_hook_settings_invalid")


def parse(content):
    if not isinstance(content, bytes) or len(content) > LIMIT:
        _invalid()
    try:
        text = content.decode("utf-8")
        # Host-compatible TOML 1.0; do not introduce parser-only extensions.
        tomllib.loads(text)
        return tomlkit.parse(text)
    except (UnicodeError, ValueError, RecursionError, ParseError):
        _invalid()


def require_json_hook_source(content):
    """Do not silently combine existing inline hooks with generated hooks.json."""
    document = parse(content)
    if "hooks" not in document:
        return
    hooks = document["hooks"]
    # Codex stores user trust/enablement for hooks.json here. This is state,
    # not an additional hook definition; preserve it without changing consent.
    if (isinstance(hooks, Mapping) and set(hooks) == {"state"}
            and isinstance(hooks["state"], Mapping)):
        return
    raise OnboardingClientError("onboarding_native_hook_inline_settings_conflict")


def merge(content, fragment):
    document = parse(content)
    require_json_hook_source(content)
    if not isinstance(fragment, dict) or set(fragment) not in ({"network"}, {"mcp_servers"}, {"network", "mcp_servers"}):
        _invalid()
    additions = 0
    expected = deepcopy(document.unwrap())
    if "mcp_servers" in fragment:
        from .native_mcp_settings import entry, check
        generated = entry(fragment, "mcp_servers")
        saved_servers = document.get("mcp_servers", {})
        if not check(saved_servers, generated):
            expected.setdefault("mcp_servers", {})["atellagent"] = deepcopy(generated)
            if "mcp_servers" not in document:
                document["mcp_servers"] = tomlkit.table()
            document["mcp_servers"]["atellagent"] = deepcopy(generated)
            additions += 1
    if "network" not in fragment:
        return _encode(document, expected, content, additions)
    network = fragment["network"]
    if not isinstance(network, dict) or set(network) != {"allow_unix_sockets"}:
        _invalid()
    sockets = network["allow_unix_sockets"]
    if not isinstance(sockets, list) or len(sockets) != 1 or not isinstance(sockets[0], str):
        _invalid()
    endpoint = sockets[0]
    path = PurePosixPath(endpoint)
    if (not path.is_absolute() or ".." in path.parts or endpoint.startswith("//")
        or any(ord(c) < 32 or c in "*?[]" for c in endpoint) or endpoint == "/"):
        _invalid()
    saved = document.get("network", {})
    if not isinstance(saved, Mapping):
        _invalid()
    allowed = saved.get("allow_unix_sockets", [])
    if not isinstance(allowed, list) or any(not isinstance(item, str) or not item for item in allowed):
        _invalid()
    if endpoint in allowed:
        return _encode(document, expected, content, additions)
    expected.setdefault("network", {}).setdefault("allow_unix_sockets", []).append(endpoint)
    if "network" not in document:
        document["network"] = tomlkit.table()
    if "allow_unix_sockets" not in document["network"]:
        document["network"]["allow_unix_sockets"] = tomlkit.array()
    document["network"]["allow_unix_sockets"].append(endpoint)
    return _encode(document, expected, content, additions + 1)


def _encode(document, expected, content, additions):
    if not additions:
        return content, 0
    try:
        output = tomlkit.dumps(document).encode("utf-8")
        if len(output) > LIMIT or tomllib.loads(output.decode("utf-8")) != expected:
            _invalid()
    except (UnicodeError, ValueError, RecursionError):
        _invalid()
    return output, additions
