"""Config-free device setup and proof-only continuation for attended customers."""

import argparse
import asyncio
import sys

from atellagent_client.sdk import DeviceSetup, OnboardingClientError, SetupManagementClient
from atellagent_client.onboarding.installation_paths import installation_directory
from .setup_login import login, select_membership
from .setup_progress import wait_for_setup


async def run(arguments, *, interaction=None):
    if arguments and arguments[0] == "connections":
        from .setup_connections import run as connections
        return await connections(arguments[1:])
    if arguments and arguments[0] == "cancel":
        from .setup_cancel import run as run_cancel
        return await run_cancel(arguments[1:])
    if arguments and arguments[0] == "archive":
        from .setup_archive import run as run_archive
        return await run_archive(arguments[1:])
    if interaction is None and arguments and arguments[0] == "desktop":
        from .setup_desktop import run as run_desktop
        return await run_desktop(arguments[1:])
    if arguments and arguments[0] == "status":
        from .setup_status import run as run_status
        return await run_status(arguments[1:])
    if arguments and arguments[0] == "native":
        from .setup_native import run as run_native
        return await run_native(arguments[1:])
    if arguments and arguments[0] == "mcp":
        from .setup_mcp import run as run_mcp
        return await run_mcp(arguments[1:])
    if arguments and arguments[0] == "requests":
        from .setup_request_batch import run as run_batch
        return await run_batch(arguments[1:])
    parser = argparse.ArgumentParser(description="Request device enrollment without a configuration file or token.",
        epilog="Connections: atellagent-cli setup native --help or atellagent-cli setup mcp --help")
    parser.add_argument("action", choices=("device", "resume"))
    parser.add_argument("--platform-url", help="Trusted Atellagent deployment HTTPS origin")
    parser.add_argument("--name", help="Device name displayed to your organization")
    parser.add_argument("--connections-file", help="Local JSON native/MCP selections to submit with the device")
    parser.add_argument("--wait-seconds", type=int, default=60, help="Wait for approval/readiness, then retain the request")
    args = parser.parse_args(arguments)
    if not 1 <= args.wait_seconds <= 900:
        parser.error("--wait-seconds must be between 1 and 900")
    if args.action == "resume" and (args.platform_url or args.name or args.connections_file):
        parser.error("resume uses the saved deployment and device identity")
    if args.action == "device" and not args.platform_url:
        parser.error("device setup requires --platform-url")
    try:
        with DeviceSetup(installation_directory()) as setup:
            if args.action == "device":
                setup.require_new()
                from .setup_request_batch import choices_for_setup, submit_batch
                selections = await choices_for_setup(args.connections_file, interaction)
                tokens = await login(args.platform_url) if interaction is None else await login(args.platform_url, interaction=interaction)
                async with SetupManagementClient(origin=args.platform_url, tokens=tokens) as management:
                    owner, tenant = (await select_membership(management) if interaction is None
                        else await select_membership(management, interaction=interaction))
                    name = args.name
                    if name is None:
                        name = (await asyncio.to_thread(input, "Device name: ") if interaction is None
                            else await interaction.ask("device_name"))
                    platform = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
                    disclosure = "Discovery reports agent presence and MCP names/transports, not configuration or credentials."
                    if interaction is None:
                        print(disclosure, flush=True)
                    else:
                        interaction.emit("reporting_disclosure", summary=disclosure)
                    draft = await setup.prepare(management, tenant_id=tenant, owner_user_id=owner,
                                                name=name, platform=platform, reporting_enabled=True)
                    if interaction is not None:
                        interaction.emit("request_saved", setup_id=str(draft.operation_id))
                    await setup.submit(management)
                    if selections["native"] or selections["mcp"]:
                        results = await submit_batch(setup, management, selections=selections, interaction=interaction)
                        if any(item["status"] != "submitted" for item in results):
                            if interaction is None:
                                print("Some requests are unconfirmed. Retry retained submissions with: atellagent-cli setup requests submit", flush=True)
                                raise SystemExit(1)
                            interaction.emit("wait_ended", state="retained")
                            return
            elif setup.request_batch().needs_submission():
                from .setup_request_batch import submit_batch
                draft, _, _ = setup.pending.load()
                tokens = (await login(draft.platform_origin) if interaction is None
                    else await login(draft.platform_origin, interaction=interaction))
                async with SetupManagementClient(origin=draft.platform_origin, tokens=tokens) as management:
                    results = await submit_batch(setup, management, interaction=interaction)
                if any(item["status"] != "submitted" for item in results):
                    if interaction is None:
                        print("Some connection submissions remain unconfirmed. Continue the retained setup to retry.", flush=True)
                        raise SystemExit(1)
                    interaction.emit("wait_ended", state="retained")
                    return
            try:
                flow = await setup.resume()
            except OnboardingClientError as error:
                if args.action != "resume" or error.code != "onboarding_setup_submission_unconfirmed":
                    raise
                # Explicit Continue may renew human authority for the exact retained intent.
                # Proof-only recovery stays first, so a lost acknowledgement needs no login.
                draft, _, _ = setup.pending.load()
                tokens = (await login(draft.platform_origin) if interaction is None
                    else await login(draft.platform_origin, interaction=interaction))
                async with SetupManagementClient(origin=draft.platform_origin, tokens=tokens) as management:
                    await setup.submit(management)
                flow = await setup.resume()
            if interaction is None:
                await wait_for_setup(flow, args.wait_seconds)
            else:
                await wait_for_setup(flow, args.wait_seconds, interaction=interaction)
    except (OnboardingClientError, OSError, ValueError, EOFError) as exc:
        code = exc.code if isinstance(exc, OnboardingClientError) else "local_setup_unavailable"
        guidance = {"onboarding_pending_setup_exists": "Continue with: atellagent-cli setup resume",
            "device_installation_already_selected": "This installation already has an enrolled device.",
            "onboarding_organization_selection_invalid": "Choose one listed organization.",
            "onboarding_membership_required": "The signed-in account needs organization membership."}.get(code,
                "Existing setup state was not replaced.")
        if interaction is None:
            print(f"Device setup failed: {code}. {guidance}", file=sys.stderr)
        else:
            interaction.emit("error", code=code)
        raise SystemExit(1) from None
