"""Local-only hook preview for the exact currently selected native request."""

import os
from pathlib import Path
import sys

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_runtime_selection import NativeRuntimeSelections
from atellagent_client.onboarding.native_local_draft import load_native_local_draft
from .native_hook_definition import definition
from .native_hook_review import prepare
from . import service_platform as service


def _review(directory, request_id, *, remove=False, hooks_only=False):
    service._require_supported_platform()
    draft = load_native_local_draft(directory, request_id)
    host = draft.proposal.native_host
    # Do not silently write a different profile from an overridden host config root.
    override = "CODEX_HOME" if host == "codex" else "CLAUDE_CONFIG_DIR"
    if os.environ.get(override):
        raise OnboardingClientError("onboarding_native_hook_profile_requires_review")
    selections = NativeRuntimeSelections(directory / "native-runtime-selections")
    selected = selections.load(host)
    if selected is None:
        raise OnboardingClientError("onboarding_native_request_not_selected")
    selections.require_draft(selected, draft)
    result = definition(draft=draft, executable=service._installed_cli_executable(),
        endpoint=service._endpoint(host), home=str(Path.home()), platform=sys.platform)
    if hooks_only:
        # Hook readiness does not depend on the separately configured MCP bridge.
        # Keep Codex's socket permission: native hooks need it on macOS too.
        files = []
        for item in result["files"]:
            fragment = {key: value for key, value in item["fragment"].items()
                        if key in {"hooks", "network"}}
            if fragment:
                files.append({**item, "fragment": fragment})
        result["files"] = files
    reviewed = prepare(result, home=Path.home(), remove=remove)
    def require_selection():
        if selections.load(host) != selected:
            raise OnboardingClientError("onboarding_native_selection_changed", retryable=True)
    require_selection()
    return reviewed, require_selection


def preview(directory, request_id, *, remove=False):
    return _review(directory, request_id, remove=remove)[0].public


def apply(directory, request_id, review_hash, *, remove=False):
    reviewed, require_selection = _review(directory, request_id, remove=remove)
    if os.name == "nt":
        from .native_hook_windows_apply import apply as write
    else:
        from .native_hook_posix_apply import apply as write
    return write(reviewed, review_hash=review_hash, require_selection=require_selection)
