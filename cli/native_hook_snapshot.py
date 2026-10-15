"""Bounded platform-specific settings snapshots, without creating any paths."""

from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

from atellagent_client.onboarding.errors import OnboardingClientError

LIMIT = 1024 * 1024


@dataclass(frozen=True)
class Snapshot:
    exists: bool
    revision: str
    content: bytes = field(repr=False)
    mode: int | None = field(default=None, repr=False)
    group: int | None = field(default=None, repr=False)
    security: str | None = field(default=None, repr=False)


def _unsafe():
    raise OnboardingClientError("onboarding_native_hook_settings_unsafe")


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns, info.st_gid)


def _read(root, path):
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    identities = []
    try:
        for index, part in enumerate(path.parts[1:]):
            last = index == len(path.parts) - 2
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if not last:
                flags |= os.O_DIRECTORY
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                # A missing home is not permission to invent a new profile.
                if index < len(root.parts) - 1:
                    _unsafe()
                return False, b"", identities + [("missing", *path.parts[index + 1:])]
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            scoped = index >= len(root.parts) - 2
            if scoped and info.st_uid != os.getuid():
                _unsafe()
            if info.st_mode & 0o022 and not (not scoped and info.st_mode & stat.S_ISVTX):
                _unsafe()
            if last:
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > LIMIT
                    or info.st_mode & 0o7000):
                    _unsafe()
            elif not stat.S_ISDIR(info.st_mode):
                _unsafe()
            # Directory size/timestamps change for unrelated sibling files.
            identities.append(_identity(info) if last else _identity(info)[:4])
        content = bytearray()
        while len(content) <= LIMIT:
            chunk = os.read(descriptor, min(65536, LIMIT + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > LIMIT or _identity(os.fstat(descriptor)) != identities[-1]:
            _unsafe()
        return True, bytes(content), identities
    finally:
        os.close(descriptor)


def snapshot(root: Path, path: Path):
    """Read current-user settings with the operating system's no-follow reader."""
    if os.name not in ("posix", "nt"):
        raise OnboardingClientError("onboarding_native_hook_snapshot_platform_unavailable")
    if not root.is_absolute() or not path.is_absolute() or ".." in root.parts or ".." in path.parts:
        _unsafe()
    if root == Path("/") or root not in path.parents:
        _unsafe()
    reader = _read
    if os.name == "nt":
        from .native_hook_windows_snapshot import read
        reader = read
    try:
        first = reader(root, path)
        if reader(root, path) != first:
            raise OnboardingClientError("onboarding_native_hook_settings_changed", retryable=True)
    except (OSError, OverflowError):
        _unsafe()
    exists, content, identities = first
    digest = sha256(json.dumps([str(path), exists, identities], separators=(",", ":")).encode())
    digest.update(content)
    mode = stat.S_IMODE(identities[-1][2]) if exists and os.name == "posix" else None
    group = identities[-1][-1] if exists and os.name == "posix" else None
    security = identities[-1][4] if exists and os.name == "nt" else None
    return Snapshot(exists, digest.hexdigest(), content, mode, group, security)


def require_unchanged(root: Path, path: Path, reviewed: Snapshot):
    if snapshot(root, path) != reviewed:
        raise OnboardingClientError("onboarding_native_hook_settings_changed", retryable=True)
