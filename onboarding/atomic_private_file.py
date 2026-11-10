# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Single-record commit with no replacement and bounded no-follow reads."""

import os
from pathlib import Path
import stat
import tempfile

from .private_paths import require_private_path, require_safe_ancestry


def create_private_file(path: Path, content: bytes) -> None:
    require_safe_ancestry(path.parent)
    require_private_path(path.parent, directory=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".setup-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as output:
            if os.name == "nt":
                from .windows_private_path import protect_new_path
                protect_new_path(temporary)
            else:
                os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        require_private_path(temporary, directory=False)
        os.link(temporary, path, follow_symlinks=False)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def read_private_file(path: Path, *, limit: int) -> bytes:
    require_safe_ancestry(path.parent)
    require_private_path(path.parent, directory=True)
    require_private_path(path, directory=False)
    before = path.lstat()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        opened = os.fstat(source.fileno())
        if ((before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or not stat.S_ISREG(opened.st_mode) or opened.st_size > limit):
            raise ValueError("stable bounded private record required")
        if os.name != "nt" and (opened.st_uid != os.getuid() or opened.st_mode & 0o077):
            raise ValueError("owner-private open record required")
        raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("bounded private record required")
        return raw
