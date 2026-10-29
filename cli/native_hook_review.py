"""Exact settings review: public generated changes, private existing content."""

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path

from .native_hook_json_merge import merge as merge_json
from .native_hook_toml_merge import merge as merge_toml, require_json_hook_source
from .native_hook_snapshot import Snapshot, snapshot, require_unchanged


@dataclass(frozen=True)
class Edit:
    path: Path
    before: Snapshot = field(repr=False)
    after: bytes = field(repr=False)


@dataclass(frozen=True)
class Review:
    public: dict
    root: Path
    edits: tuple[Edit, ...] = field(repr=False)
    guards: tuple[tuple[Path, Snapshot], ...] = field(repr=False)

    def require_unchanged(self):
        for edit in self.edits:
            require_unchanged(self.root, edit.path, edit.before)
        for path, before in self.guards:
            require_unchanged(self.root, path, before)


def prepare(definition, *, home, remove=False):
    """Build from the generated definition only; never return merged content publicly."""
    root = Path(home)
    result = deepcopy(definition)
    if remove:
        result["operation"] = "remove"
    edits, guards = [], []
    host = result["native_host"]
    directory = root / (".codex" if host == "codex" else ".claude")
    json_path = directory / ("hooks.json" if host == "codex" else "settings.json")
    config_path = directory / "config.toml"
    allowed = {json_path: "json"}
    if host == "codex":
        allowed[config_path] = "toml"
    else:
        allowed[root / ".claude.json"] = "json"
    seen = set()
    for item in result["files"]:
        path = Path(item["path"])
        if path in seen or allowed.get(path) != item["format"]:
            raise ValueError("generated current-user settings targets required")
        seen.add(path)
        before = snapshot(root, path)
        content = before.content if before.exists else (b"{}" if item["format"] == "json" else b"")
        merge = merge_json if item["format"] == "json" else merge_toml
        if remove:
            from .native_hook_json_remove import remove as remove_json
            from .native_hook_toml_remove import remove as remove_toml
            merge = remove_json if item["format"] == "json" else remove_toml
        if path == root / ".claude.json":
            from .native_mcp_settings import merge_json as merge_mcp
            after, additions = merge_mcp(content, item["fragment"], remove=remove)
        else:
            after, additions = merge(content, item["fragment"])
        if remove and not before.exists:
            after = before.content
        edits.append(Edit(path, before, after))
        item.update(exists=before.exists, revision=before.revision,
            **({"removals": additions} if remove else {"additions": additions}),
            changed=after != before.content)
    if json_path not in seen:
        raise ValueError("generated hook settings required")
    result["checks"] = []
    # Windows still reads config.toml for inline-hook overlap, although it does
    # not need a Unix socket permission edit there.
    if host == "codex" and config_path not in seen:
        before = snapshot(root, config_path)
        require_json_hook_source(before.content)
        guards.append((config_path, before))
        result["checks"].append(dict(path=str(config_path), exists=before.exists,
            revision=before.revision, purpose="inline_hooks_absent"))
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    result["review_sha256"] = sha256(canonical).hexdigest()
    review = Review(result, root, tuple(edits), tuple(guards))
    review.require_unchanged()
    return review
