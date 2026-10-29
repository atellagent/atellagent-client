# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Owner-private files used by current-user OS service registration."""

import os
from pathlib import Path
import stat
import sys
import tempfile

from .service_errors import ServiceCommandError
from atellagent_client.onboarding.private_paths import require_safe_ancestry


def _owned_directory(path: Path, *, private: bool) -> None:
    if path.is_symlink():
        raise ServiceCommandError(f"Refusing symlinked service directory: {path}")
    path.mkdir(mode=0o700 if private else 0o755, parents=True, exist_ok=True)
    if sys.platform == "win32":
        from atellagent_client.cli.windows_service import secure_user_path

        secure_user_path(path)
        return
    info = path.stat()
    if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
        raise ServiceCommandError(f"Service directory is not owned by this user: {path}")
    if stat.S_IMODE(info.st_mode) & (0o077 if private else 0o022):
        raise ServiceCommandError(f"Service directory must be private: {path}")

def _read_owned_file(path: Path) -> bytes | None:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        require_safe_ancestry(path.parent)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError("bounded regular service file required")
        if sys.platform == "win32":
            from atellagent_client.onboarding.windows_credentials import require_current_user_acl
            require_current_user_acl(path)
        elif info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("owner-private service file required")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            current = os.fstat(source.fileno())
            if ((info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
                    or not stat.S_ISREG(current.st_mode)):
                raise ValueError("stable service file required")
            value = source.read(65537)
            if len(value) > 65536:
                raise ValueError("bounded service file required")
            return value
    except (OSError, ValueError) as exc:
        raise ServiceCommandError(f"Refusing an unsafe service file: {path}") from exc

def _write_private_file(path: Path, data: bytes, *, private_parent: bool = True) -> None:
    _owned_directory(path.parent, private=private_parent)
    _read_owned_file(path)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        if sys.platform == "win32":
            from atellagent_client.cli.windows_service import secure_user_path

            secure_user_path(Path(temporary))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
