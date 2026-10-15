"""Current-user installation state shared by CLI and packaged applications."""

import os
from pathlib import Path
import sys

from atellagent_client.onboarding.private_paths import create_private_directory


def installation_directory() -> Path:
    if os.name == "nt":
        location = os.environ.get("LOCALAPPDATA")
        if not location:
            raise ValueError("Windows current-user storage is unavailable")
        parent = Path(location)
    elif sys.platform == "darwin":
        parent = Path.home() / "Library" / "Application Support"
    else:
        parent = Path.home()
        for component in (".local", "share"):
            parent = parent / component
            if not parent.exists():
                create_private_directory(parent)
    # Existing OS profile directories are not chmodded or repaired.
    root = parent / "Atellagent"
    create_private_directory(root)
    return root / "installation"
