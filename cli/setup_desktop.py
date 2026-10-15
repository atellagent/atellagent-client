"""Desktop transport for the same device setup flow, not another authorization path."""

import argparse

from .setup_interaction import SetupInteraction


async def run(arguments):
    if arguments and arguments[0] == "mcp":
        parser = argparse.ArgumentParser(prog="atellagent-cli setup desktop mcp")
        if len(arguments) < 2 or arguments[1] not in {"request", "resume", "submit", "start", "stop", "pickup", "bootstrap"}:
            parser.error("Choose request, resume, submit, start, stop, pickup or bootstrap; foreground runtime is not a UI operation")
        from .setup_mcp import run as run_mcp
        return await run_mcp(arguments[1:], interaction=SetupInteraction())
    if arguments and arguments[0] == "native":
        parser = argparse.ArgumentParser(prog="atellagent-cli setup desktop native")
        if len(arguments) < 2 or arguments[1] not in {"request", "resume", "submit", "consent", "select", "pickup"}:
            parser.error("choose request, resume, submit, consent, select or pickup; foreground runtime is not a UI operation")
        from .setup_native import run as run_native
        return await run_native(arguments[1:], interaction=SetupInteraction())
    parser = argparse.ArgumentParser(prog="atellagent-cli setup desktop")
    parser.add_argument("action", choices=("device", "resume"))
    parser.add_argument("--platform-url")
    parser.add_argument("--name")
    parser.add_argument("--wait-seconds", type=int, default=60)
    args = parser.parse_args(arguments)
    if not 1 <= args.wait_seconds <= 900:
        parser.error("--wait-seconds must be between 1 and 900")
    if args.action == "device" and not args.platform_url:
        parser.error("device requires --platform-url")
    if args.action == "resume" and (args.platform_url or args.name):
        parser.error("resume uses the retained setup identity")
    forwarded = [args.action, "--wait-seconds", str(args.wait_seconds)]
    if args.platform_url is not None:
        forwarded.extend(["--platform-url", args.platform_url])
    if args.name is not None:
        forwarded.extend(["--name", args.name])
    from .setup_device import run as run_device
    await run_device(forwarded, interaction=SetupInteraction())
