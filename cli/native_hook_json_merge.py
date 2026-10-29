"""Pure JSON settings merge; no filesystem access or implicit hook replacement."""

from copy import deepcopy
import json

from atellagent_client.onboarding.errors import OnboardingClientError

LIMIT = 1024 * 1024


def _reject():
    raise OnboardingClientError("onboarding_native_hook_settings_invalid")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _reject()
        result[key] = value
    return result


def parse(content):
    """Reject ambiguous JSON without putting existing settings in diagnostics."""
    if not isinstance(content, bytes) or len(content) > LIMIT:
        _reject()
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=lambda _: _reject())
    except (UnicodeError, ValueError, RecursionError):
        _reject()
    if not isinstance(value, dict):
        _reject()
    return value


def _groups(hooks):
    if not isinstance(hooks, dict):
        _reject()
    for event, groups in hooks.items():
        if not isinstance(event, str) or not isinstance(groups, list):
            _reject()
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                _reject()
            if any(not isinstance(handler, dict) for handler in group["hooks"]):
                _reject()
    return hooks


def merge(content, fragment):
    """Return merged bytes and addition count; preserve originals on a no-op.

    A command already present under a different matcher/timeout/handler is a
    review conflict, not permission to silently replace it. Old installations
    with different commands require a separate explicit replacement operation.
    """
    original = parse(content)
    result = deepcopy(original)
    if not isinstance(fragment, dict) or set(fragment) != {"hooks"}:
        _reject()
    generated = _groups(fragment["hooks"])
    existing = _groups(result.get("hooks", {}))
    additions = 0
    for event, groups in generated.items():
        target = existing.setdefault(event, [])
        for group in groups:
            if len(group["hooks"]) != 1:
                _reject()
            handler = group["hooks"][0]
            if handler.get("type") != "command" or not isinstance(handler.get("command"), str) or not handler["command"]:
                _reject()
            matches = [saved for saved in target if any(
                item.get("type") == "command" and item.get("command") == handler["command"]
                for item in saved["hooks"])]
            if matches:
                if len(matches) != 1 or matches[0] != group:
                    raise OnboardingClientError("onboarding_native_hook_settings_conflict")
                continue
            target.append(deepcopy(group))
            additions += 1
    if not additions:
        return content, 0
    result["hooks"] = existing
    try:
        encoded = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, RecursionError, UnicodeError):
        _reject()
    if len(encoded) > LIMIT:
        _reject()
    return encoded, additions
