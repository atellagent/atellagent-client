"""Exact local MCP entry merge; names are configuration keys, not authority."""

from copy import deepcopy
import json
from collections.abc import Mapping

from atellagent_client.onboarding.errors import OnboardingClientError
from .native_hook_json_merge import parse, LIMIT, _reject
from .native_hook_commands import command


def entry(fragment, key):
    servers = fragment.get(key)
    if not isinstance(servers, dict) or set(servers) != {"atellagent"}:
        _reject()
    generated = servers["atellagent"]
    expected = {"command", "args", "type", "alwaysLoad"} if key == "mcpServers" else {"command", "args", "startup_timeout_sec"}
    if not isinstance(generated, dict) or set(generated) != expected:
        _reject()
    argv = generated["args"]
    if (not isinstance(argv, list) or len(argv) != 3 or argv[:2] != ["mcp-bridge", "--socket"]
            or (key == "mcpServers" and (generated["type"] != "stdio"
                or type(generated["alwaysLoad"]) is not bool or generated["alwaysLoad"] is not True))
            or (key == "mcp_servers" and (type(generated["startup_timeout_sec"]) is not int
                or generated["startup_timeout_sec"] != 30))):
        _reject()
    executable = generated["command"]
    try:
        command([executable, "hook", "--host", "codex", "--socket", argv[2]],
            platform="darwin" if isinstance(executable, str) and executable.startswith("/") else "win32",
            host="codex")
    except (ValueError, TypeError):
        _reject()
    return generated


def _same_entry(saved, generated):
    if not isinstance(saved, Mapping):
        return False
    try:
        return json.dumps(dict(saved), sort_keys=True, allow_nan=False) == json.dumps(generated, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return False


def check(saved, generated):
    if not isinstance(saved, Mapping):
        _reject()
    if "atellagent" in saved and not _same_entry(saved["atellagent"], generated):
        raise OnboardingClientError("onboarding_native_mcp_settings_conflict")
    return "atellagent" in saved


def merge_json(content, fragment, *, remove=False):
    if not isinstance(fragment, dict) or set(fragment) != {"mcpServers"}:
        _reject()
    generated = entry(fragment, "mcpServers")
    document = parse(content)
    saved = document.get("mcpServers", {})
    # The review shows this one-field loading update before any settings write.
    # Only the exact prior managed launch qualifies; explicit preferences,
    # different endpoints, extra fields and credentials still conflict.
    prior_launch = {k: v for k, v in generated.items() if k != "alwaysLoad"}
    loading_update = isinstance(saved, Mapping) and _same_entry(saved.get("atellagent"), prior_launch)
    present = True if loading_update else check(saved, generated)
    if present == (not remove) and not loading_update:
        return content, 0
    result = deepcopy(document)
    if remove:
        del result["mcpServers"]["atellagent"]
        if not result["mcpServers"]:
            del result["mcpServers"]
    else:
        result.setdefault("mcpServers", {})["atellagent"] = deepcopy(generated)
    output = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    if len(output) > LIMIT:
        _reject()
    parse(output)
    return output, 1
