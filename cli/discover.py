# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Command-line presentation for read-only discovery."""

import argparse
import json
from pathlib import Path
import sys

from atellagent_client.discovery.inventory import discover


def run(arguments: list[str]) -> None:
    if arguments and arguments[0] == "probe":
        from .discovery_probe import run as run_probe

        run_probe(arguments[1:])
        return
    parser = argparse.ArgumentParser(description="Inspect current-user coding-host configuration without changing it.")
    parser.add_argument("--project", type=Path, help="Also inspect the selected project directory (no recursive scan)")
    parser.add_argument("--json", action="store_true", help="Emit the redacted machine-readable inventory")
    args = parser.parse_args(arguments)
    if args.project is not None:
        print(f"Inspecting selected project: {json.dumps(str(args.project.expanduser().absolute()))}", file=sys.stderr)
    try:
        result = discover(home=Path.home(), project=args.project)
    except (OSError, ValueError):
        parser.error("The selected project is not an accessible directory")
    if args.json:
        json.dump(result, sys.stdout, sort_keys=True, indent=2)
        sys.stdout.write("\n")
    else:
        from .discovery_view import format_inventory

        print(format_inventory(result))


__all__ = ["discover", "run"]
