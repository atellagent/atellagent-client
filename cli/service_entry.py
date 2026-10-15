"""Windowless entry point for the Windows current-user supervisor."""

from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("This entry point is for Windows only")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("Windows user profile storage is unavailable")
    directory = Path(local_app_data) / "Atellagent" / "services"
    if directory.is_symlink():
        raise RuntimeError("Refusing symlinked service directory")
    directory.mkdir(parents=True, exist_ok=True)

    from atellagent_client.cli.windows_service import secure_user_path

    secure_user_path(directory)
    log_path = directory / "supervisor.log"
    if log_path.is_symlink():
        raise RuntimeError("Refusing symlinked service log")
    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    secure_user_path(log_path)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler],
        force=True,
    )
    # A PyInstaller windowless process has no standard streams. Library warnings
    # must not crash the supervisor before a controller can open its pipe.
    sys.stdin = open(os.devnull, "r", encoding="utf-8")
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    sys.stderr = sys.stdout

    from atellagent_client.cli.supervisor import run

    logging.warning("Local controller supervisor starting")
    try:
        asyncio.run(run())
    except Exception:
        logging.exception("Local controller supervisor exited unexpectedly")
        raise


if __name__ == "__main__":
    main()
