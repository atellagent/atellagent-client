"""Lightweight command dispatch; no imported machine configuration or enrollment token."""

import argparse
import asyncio
from contextlib import suppress
import logging
import sys
from atellagent_client.onboarding.errors import OnboardingClientError


async def _async_main(arguments=None):
    parser = argparse.ArgumentParser(description="Atellagent setup and connected runtime commands.")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("command", choices=("setup", "workload", "discover", "service"))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(sys.argv[1:] if arguments is None else arguments)
    logging.basicConfig(level=logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "setup":
        from .setup_device import run
    elif args.command == "workload":
        from .workload import run
    elif args.command == "discover":
        if args.arguments[:1] == ["reporting"]:
            from .discovery_reporting import run
            return await run(args.arguments[1:])
        from .discover import run
        return run(args.arguments)
    elif args.arguments == ["run"]:
        from .supervisor import run
        return await run()
    else:
        from .native_service import run
    return await run(args.arguments)


def main():
    with suppress(KeyboardInterrupt):
        try:
            asyncio.run(_async_main())
        except (OnboardingClientError, OSError, ValueError, ImportError, AttributeError) as exc:
            code = exc.code if isinstance(exc, OnboardingClientError) else "local_command_failed"
            print(f"[ERROR] {code}. Saved setup and credentials were not replaced.", file=sys.stderr)
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
