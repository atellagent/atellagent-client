"""Lightweight command dispatch; no imported machine configuration or enrollment token."""

import argparse
import asyncio
from contextlib import suppress
import logging
import sys
from atellagent_client.onboarding.errors import OnboardingClientError


async def _run_workload(arguments):
    from .workload import run
    return await run(arguments)


async def _run_discover(arguments):
    from .discover import run
    return run(arguments)


PUBLIC_COMMANDS = {"workload": _run_workload, "discover": _run_discover}


async def _async_main(arguments=None, *, commands=None):
    commands = PUBLIC_COMMANDS if commands is None else commands
    parser = argparse.ArgumentParser(description="Atellagent client workload and discovery commands.")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("command", choices=tuple(commands))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(sys.argv[1:] if arguments is None else arguments)
    logging.basicConfig(level=logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return await commands[args.command](args.arguments)


def main(*, commands=None):
    with suppress(KeyboardInterrupt):
        try:
            asyncio.run(_async_main(commands=commands))
        except (OnboardingClientError, OSError, ValueError, ImportError, AttributeError) as exc:
            code = exc.code if isinstance(exc, OnboardingClientError) else "local_command_failed"
            print(f"[ERROR] {code}. Saved setup and credentials were not replaced.", file=sys.stderr)
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
