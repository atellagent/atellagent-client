# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Owner-private local paths; unsafe existing state is rejected, never repaired."""

import os
from pathlib import Path
import stat


def require_private_path(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    if (path.is_symlink() or getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise ValueError("regular owner-private path required")
    if os.name == "nt":
        from .windows_credentials import require_current_user_acl
        require_current_user_acl(path)
    elif info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("owner-private path required")


def require_safe_ancestry(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError("absolute local state directory required")
    for parent in (path, *path.parents):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or parent.is_symlink()
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise ValueError("local state ancestry must not contain links")
        if os.name != "nt" and info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
            raise ValueError("local state ancestry must not permit directory replacement")


def create_private_directory(path: Path, *, exclusive: bool = False) -> None:
    require_safe_ancestry(path.parent)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        if exclusive:
            raise
    else:
        if os.name == "nt":
            from .windows_private_path import protect_new_path
            protect_new_path(path)
    require_safe_ancestry(path)
    require_private_path(path, directory=True)
