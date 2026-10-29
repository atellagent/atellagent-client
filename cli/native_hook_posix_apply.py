"""Explicit POSIX settings application; each file replacement is atomic."""

from contextlib import contextmanager
import os
import secrets

from atellagent_client.onboarding.errors import OnboardingClientError
from .native_hook_snapshot import snapshot, require_unchanged
from .native_hook_apply_result import ApplyError


@contextmanager
def _directory(path):
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def apply(review, *, review_hash, require_selection):
    """No multi-file transaction claim: report files committed before a failure.

    Rechecks catch settings edited since preview or while staging. Like ordinary
    editors, check-and-rename is not an OS compare-and-swap against another
    same-user editor racing the final rename. No automatic rollback overwrites
    subsequent user edits.
    """
    if os.name != "posix":
        raise OnboardingClientError("onboarding_native_hook_apply_platform_unavailable")
    if review_hash != review.public["review_sha256"]:
        raise OnboardingClientError("onboarding_native_hook_review_changed", retryable=True)
    written = []
    try:
        require_selection()
        review.require_unchanged()
        if not any(edit.after != edit.before.content for edit in review.edits):
            return dict(request_id=review.public["request_id"], written=[],
                settings="saved", readiness="not_verified")
        parents = {edit.path.parent for edit in review.edits}
        if any(parent != review.root and parent.parent != review.root for parent in parents):
            raise ValueError("current-user host directory required")
        created = set()
        with _directory(review.root) as root_descriptor:
            for parent in parents - {review.root}:
                try:
                    os.mkdir(parent.name, mode=0o700, dir_fd=root_descriptor)
                    created.add(parent)
                except FileExistsError:
                    pass
        # Only our directory creation may change an absent-path snapshot.
        baselines = {}
        for path, before in [(edit.path, edit.before) for edit in review.edits] + list(review.guards):
            if path.parent in created and not before.exists:
                baseline = snapshot(review.root, path)
                if baseline.exists:
                    raise OnboardingClientError("onboarding_native_hook_settings_changed", retryable=True)
                baselines[path] = baseline
            else:
                require_unchanged(review.root, path, before)
                baselines[path] = before
        for edit in review.edits:
            with _directory(edit.path.parent) as descriptor:
                if edit.after == edit.before.content:
                    continue
                name = ".atellagent-hook-" + secrets.token_hex(16)
                try:
                    output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600, dir_fd=descriptor)
                    with os.fdopen(output, "wb") as stream:
                        stream.write(edit.after)
                        stream.flush()
                        if edit.before.exists:
                            os.fchown(stream.fileno(), -1, edit.before.group)
                            os.fchmod(stream.fileno(), edit.before.mode)
                        os.fsync(stream.fileno())
                    require_selection()
                    for path, before in baselines.items():
                        require_unchanged(review.root, path, before)
                    os.replace(name, edit.path.name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
                    written.append(edit.path)
                    os.fsync(descriptor)
                    baselines[edit.path] = snapshot(review.root, edit.path)
                    if baselines[edit.path].content != edit.after:
                        raise OnboardingClientError("onboarding_native_hook_settings_changed", retryable=True)
                finally:
                    try:
                        os.unlink(name, dir_fd=descriptor)
                    except FileNotFoundError:
                        pass
    except (OnboardingClientError, OSError, ValueError) as error:
        code = error.code if isinstance(error, OnboardingClientError) else "onboarding_native_hook_apply_failed"
        raise ApplyError(code, written) from None
    return dict(request_id=review.public["request_id"], written=[str(path) for path in written],
        settings="saved", readiness="not_verified")
