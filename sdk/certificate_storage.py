"""Fsynced local staging for certificate rotation material."""

from contextlib import suppress
import os
import tempfile
from pathlib import Path


def stage_file(path: Path, content: bytes, mode: int) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(staged_name)
    try:
        if os.name == "nt":
            from atellagent_client.sdk.windows_security import secure_user_path

            secure_user_path(staged)
        else:
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return staged
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        staged.unlink(missing_ok=True)
        raise
