# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Codex PostToolUse command launcher.

Codex can invoke a command hook directly for prompt and pre-tool events. Its
post-tool command path can terminate a console-script process before the
adapter has finished relaying the host event. This launcher consumes the event
once and relays its bytes unchanged to the ordinary hook adapter in a child
Python process. It deliberately contains no policy, credential, inspection,
or logging behavior.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    """Relay one Codex PostToolUse event to the normal hook adapter."""

    parser = argparse.ArgumentParser(description="Atellagent Codex post-tool adapter")
    parser.add_argument("--socket", required=True, help="absolute local hook-control socket")
    args = parser.parse_args(argv)
    payload = sys.stdin.buffer.read()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "atellagent_client.integrations.agents.host_hooks",
                "--host",
                "codex",
                "--socket",
                args.socket,
            ],
            input=payload,
            check=False,
        )
    except OSError:
        raise SystemExit(2) from None
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
