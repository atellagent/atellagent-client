"""Explicit Windows settings replacement under pinned user-directory handles."""

import os
import tempfile
from pathlib import Path

from atellagent_client.onboarding.errors import OnboardingClientError
from .native_hook_apply_result import ApplyError
from .native_hook_snapshot import snapshot, require_unchanged
from .native_hook_windows_directory import pinned_directory, protect_created
from .native_hook_windows_replace import replace_existing


def apply(review, *, review_hash, require_selection):
    if os.name != "nt":
        raise OnboardingClientError("onboarding_native_hook_apply_platform_unavailable")
    if review_hash != review.public["review_sha256"]:
        raise OnboardingClientError("onboarding_native_hook_review_changed", retryable=True)
    import pywintypes
    import win32file
    written = []
    try:
        require_selection()
        review.require_unchanged()
        if not any(edit.after != edit.before.content for edit in review.edits):
            return dict(request_id=review.public["request_id"], written=[],
                settings="saved", readiness="not_verified")
        parents = {edit.path.parent for edit in review.edits}
        if len(parents) != 1 or any(parent.parent != review.root for parent in parents):
            raise ValueError("current-user host directory required")
        parent = next(iter(parents))
        created = False
        with pinned_directory(review.root, review.root):
            try:
                parent.mkdir()
                created = True
                protect_created(parent)
            except FileExistsError:
                pass
        with pinned_directory(review.root, parent):
            baselines = {}
            for path, before in [(edit.path, edit.before) for edit in review.edits] + list(review.guards):
                if created and not before.exists:
                    baseline = snapshot(review.root, path)
                    if baseline.exists:
                        raise OnboardingClientError("onboarding_native_hook_settings_changed", retryable=True)
                    baselines[path] = baseline
                else:
                    require_unchanged(review.root, path, before)
                    baselines[path] = before
            for edit in review.edits:
                if edit.before.exists and edit.after == edit.before.content:
                    continue
                descriptor, name = tempfile.mkstemp(prefix=".atellagent-hook-", dir=parent)
                temporary = Path(name)
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        protect_created(temporary)
                        output.write(edit.after)
                        output.flush()
                        os.fsync(output.fileno())
                    require_selection()
                    for path, before in baselines.items():
                        require_unchanged(review.root, path, before)
                    if edit.before.exists:
                        # Preserve the original ACL/inheritance and attributes
                        # through the native replacement API, not ACL copying.
                        replace_existing(edit.path, temporary)
                    else:
                        # Do not overwrite an unexpected newly created file.
                        win32file.MoveFileEx(str(temporary), str(edit.path), win32file.MOVEFILE_WRITE_THROUGH)
                    written.append(edit.path)
                    baselines[edit.path] = snapshot(review.root, edit.path)
                    if baselines[edit.path].content != edit.after:
                        raise OnboardingClientError("onboarding_native_hook_settings_changed", retryable=True)
                finally:
                    temporary.unlink(missing_ok=True)
    except (OnboardingClientError, OSError, ValueError, pywintypes.error) as error:
        code = error.code if isinstance(error, OnboardingClientError) else "onboarding_native_hook_apply_failed"
        raise ApplyError(code, written) from None
    return dict(request_id=review.public["request_id"], written=[str(path) for path in written],
        settings="saved", readiness="not_verified")
