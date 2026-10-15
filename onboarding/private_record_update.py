# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Atomic updates of bounded private records; callers serialize with a lease."""

import os
from pathlib import Path
import tempfile

from .atomic_private_file import read_private_file, sync_directory
from .private_paths import require_private_path, require_safe_ancestry


def replace_private_record(path: Path, content: bytes, *, limit: int) -> None:
    if len(content) > limit:
        raise ValueError("bounded private record required")
    require_safe_ancestry(path.parent)
    require_private_path(path.parent, directory=True)
    try:
        read_private_file(path, limit=limit)
    except FileNotFoundError:
        pass
    descriptor, name = tempfile.mkstemp(prefix=".report-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            if os.name == "nt":
                from .windows_private_path import protect_new_path
                protect_new_path(temporary)
            else:
                os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        require_private_path(temporary, directory=False)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
