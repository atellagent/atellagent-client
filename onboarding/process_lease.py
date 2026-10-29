# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Nonblocking process lease released by the OS, never a stale lock sentinel."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path

from .atomic_private_file import create_private_file
from .private_paths import require_private_path, require_safe_ancestry


@contextmanager
def process_lease(path: Path):
    require_safe_ancestry(path.parent)
    require_private_path(path.parent, directory=True)
    try:
        create_private_file(path, b"0")
    except FileExistsError:
        pass
    require_private_path(path, directory=False)
    before = path.lstat()
    descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("stable private process lease required")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            yield False
            return
        yield True
    finally:
        os.close(descriptor)
