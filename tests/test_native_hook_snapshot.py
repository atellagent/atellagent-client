"""No-follow snapshots and review invalidation using disposable user profiles."""

import os
from pathlib import Path

import pytest

from atellagent_client.cli.native_hook_snapshot import LIMIT, require_unchanged, snapshot
from atellagent_client.cli import native_hook_snapshot
from atellagent_client.onboarding.errors import OnboardingClientError

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor reader")


@pytest.fixture
def profile(tmp_path):
    root = tmp_path.resolve()
    directory = root / ".codex"
    directory.mkdir(mode=0o700)
    return root, directory / "hooks.json"


def test_missing_file_and_directory_are_read_only_snapshots(profile):
    root, path = profile
    first = snapshot(root, path)
    assert not first.exists and first.content == b""
    require_unchanged(root, path, first)
    path.parent.rmdir()
    second = snapshot(root, path)
    assert not second.exists and second.revision != first.revision
    assert not path.parent.exists()


def test_snapshot_preserves_private_content_without_repr_and_tracks_edit(profile):
    root, path = profile
    path.write_bytes(b'{"secret":"hidden"}')
    path.chmod(0o644)
    reviewed = snapshot(root, path)
    assert reviewed.content == path.read_bytes() and "hidden" not in repr(reviewed)
    require_unchanged(root, path, reviewed)
    path.write_bytes(b'{}')
    with pytest.raises(OnboardingClientError, match="settings_changed"):
        require_unchanged(root, path, reviewed)


@pytest.mark.parametrize("kind", ["file_link", "directory_link", "hardlink", "writable", "fifo", "oversized"])
def test_unsafe_paths_rejected_without_repair(profile, kind):
    root, path = profile
    if kind == "file_link":
        other = root / "other"
        other.write_bytes(b"secret")
        path.symlink_to(other)
    elif kind == "directory_link":
        path.parent.rmdir()
        other = root / "other"
        other.mkdir()
        path.parent.symlink_to(other, target_is_directory=True)
    elif kind == "fifo": os.mkfifo(path)
    else:
        path.write_bytes(b"x" * (LIMIT + 1) if kind == "oversized" else b"{}")
        if kind == "hardlink": os.link(path, root / "other")
        if kind == "writable": path.chmod(0o666)
    with pytest.raises(OnboardingClientError, match="settings_unsafe"):
        snapshot(root, path)


def test_identical_replacement_still_invalidates_review(profile):
    root, path = profile
    path.write_bytes(b"{}")
    reviewed = snapshot(root, path)
    replacement = root / "new"
    replacement.write_bytes(b"{}")
    replacement.replace(path)
    assert snapshot(root, path).revision != reviewed.revision


def test_outside_profile_and_missing_root_rejected(profile):
    root, path = profile
    for candidate in (root.parent / "outside", root / ".." / "outside"):
        with pytest.raises(OnboardingClientError): snapshot(root, candidate)
    with pytest.raises(OnboardingClientError): snapshot(root / "absent", root / "absent" / "settings.json")


def test_change_between_capture_passes_is_retryable(profile, monkeypatch):
    root, path = profile
    path.write_bytes(b"{}")
    read = native_hook_snapshot._read
    calls = 0
    def changing(*args):
        nonlocal calls
        result = read(*args)
        calls += 1
        if calls == 1:
            path.write_bytes(b'{"edited":true}')
        return result
    monkeypatch.setattr(native_hook_snapshot, "_read", changing)
    with pytest.raises(OnboardingClientError, match="settings_changed") as error:
        snapshot(root, path)
    assert error.value.retryable


def test_writable_profile_directory_rejected_without_chmod(profile):
    root, path = profile
    path.parent.chmod(0o777)
    with pytest.raises(OnboardingClientError, match="settings_unsafe"):
        snapshot(root, path)
    assert path.parent.stat().st_mode & 0o777 == 0o777
