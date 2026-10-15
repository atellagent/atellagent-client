"""Literal guarded hook commands for the current desktop execution platform."""

import base64
from pathlib import PurePosixPath, PureWindowsPath
import shlex

from atellagent_client.integrations.agents.windows_named_pipe import validate_windows_named_pipe


def command(argv, *, platform, host):
    if platform not in {"darwin", "win32"} or host not in {"codex", "claude-code"}:
        raise ValueError("supported desktop hook platform and host required")
    if len(argv) != 6 or argv[1:5] != ["hook", "--host", host, "--socket"]:
        raise ValueError("exact guarded hook command required")
    if any(not isinstance(item, str) or not item or len(item) > 4096
           or any(ord(character) < 32 for character in item) for item in argv):
        raise ValueError("bounded literal hook arguments required")
    path = PureWindowsPath if platform == "win32" else PurePosixPath
    if (not path(argv[0]).is_absolute() or ".." in path(argv[0]).parts
            or (platform == "win32" and path(argv[0]).drive.startswith("\\\\"))):
        raise ValueError("absolute installed executable required")
    if platform == "darwin":
        if not PurePosixPath(argv[-1]).is_absolute() or ".." in PurePosixPath(argv[-1]).parts:
            raise ValueError("absolute local socket required")
        return {"command": shlex.join(argv)}
    validate_windows_named_pipe(argv[-1])
    literal = lambda value: "'" + value.replace("'", "''") + "'"
    script = ("$ErrorActionPreference='Stop'; try { & " + " ".join(literal(item) for item in argv)
        + "; if ($LASTEXITCODE -eq 0) { exit 0 }; exit 2 } catch { "
        + "[Console]::Error.WriteLine('Atellagent hook could not start; request denied.'); exit 2 }")
    if host == "claude-code":
        return {"shell": "powershell", "command": script}
    # The explicit shell also works when Codex's surrounding command shell differs.
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return {"command": "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded}
