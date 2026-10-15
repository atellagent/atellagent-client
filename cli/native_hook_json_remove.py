"""Remove only exact reviewed generated hooks, preserving unrelated JSON settings."""

import json

from .native_hook_json_merge import merge, parse


def remove(content, fragment):
    # Reuse strict parsing and command-collision checks. The merged result is
    # deliberately discarded: missing generated entries are never added.
    merge(content, fragment)
    result = parse(content)
    hooks = result.get("hooks", {})
    removed = 0
    for event, generated in fragment["hooks"].items():
        existing = hooks.get(event, [])
        remaining = [group for group in existing if group not in generated]
        removed += len(existing) - len(remaining)
        if remaining:
            hooks[event] = remaining
        elif event in hooks and len(existing) != len(remaining):
            del hooks[event]
    if not removed:
        return content, 0
    # Keep the hooks container and every unrelated setting, including empty
    # groups the user already had. This operation never deletes settings files.
    output = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    parse(output)
    return output, removed
