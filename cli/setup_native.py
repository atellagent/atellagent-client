"""Attended native requests and explicit consent; proof-only resume keeps identity."""

import sys
import json

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.sdk import NativeSetupContext, SetupManagementClient
from atellagent_client.onboarding.installation_paths import installation_directory
from .setup_login import login
from .setup_native_consent import consent
from .setup_native_options import options
from .setup_native_progress import wait_for_native


async def run(arguments, *, interaction=None):
    if interaction is None and arguments and arguments[0] in {"saved", "preparations"}:
        if arguments[0] == "saved":
            from .setup_native_saved import run as inventory
        else:
            from .setup_native_preparations import run as inventory
        return await inventory(arguments[1:])
    args = options(arguments)
    request_id = getattr(args, "request_id", None)
    try:
        if args.action == "hooks":
            from .setup_native_hooks import preview, apply
            result = (preview(installation_directory(), request_id, remove=args.remove) if args.apply_review is None
                else apply(installation_directory(), request_id, args.apply_review, remove=args.remove))
            print(json.dumps(result, sort_keys=True), flush=True)
            return
        if args.action == "unselect":
            NativeSetupContext.stop_selected(installation_directory(), request_id)
            print("Local selection removed. A running supervisor stops this controller on its next selection check. "
                  "Device reporting, other controllers, saved credentials and the server account remain.", flush=True)
            return
        directory = (args.installation_directory
            if args.action == "run" and args.installation_directory is not None
            else installation_directory())
        async with await NativeSetupContext.open(directory) as context:
            if args.action == "run":
                from .native_runtime import run as run_runtime
                await run_runtime(context, request_id, socket_path=args.hook_control_socket, verify=args.verify)
                return
            if args.action == "request":
                draft = context.prepare_request(display_name=args.name, packaging=args.packaging,
                    native_host=args.host, enforcement_mode=args.enforcement_mode, coverage_posture=args.coverage_posture)
                request_id = draft.proposal.id
                if interaction is None:
                    print(f"Native setup saved: {request_id}", flush=True)
                else:
                    interaction.emit("native_request_saved", request_id=str(request_id))
            elif args.action == "pickup":
                await context.pickup_preparation(request_id)
                draft = context.load(request_id)
                if interaction is None:
                    print("Prepared connection requested. Administrator approval and owner consent remain required.", flush=True)
                else:
                    interaction.emit("native_request_submitted", request_id=str(request_id))
            else:
                draft = context.load(request_id)
            if args.action == "select":
                await context.select(request_id)
                if interaction is None:
                    print("Controller selected. Service installation, startup and hook readiness are separate steps.", flush=True)
                else:
                    interaction.emit("native_controller_selected", request_id=str(request_id))
                return
            if args.action in {"request", "submit"}:
                tokens = (await login(draft.platform_origin) if interaction is None
                    else await login(draft.platform_origin, interaction=interaction))
                async with SetupManagementClient(origin=draft.platform_origin, tokens=tokens) as management:
                    await context.submit_request(management, request_id)
                if interaction is None:
                    print("Connection requested. Administrator approval and owner consent are still required.", flush=True)
                else:
                    interaction.emit("native_request_submitted", request_id=str(request_id))
            elif args.action == "consent":
                accepted = (await consent(context, draft) if interaction is None
                    else await consent(context, draft, interaction=interaction))
                if not accepted:
                    return
            if interaction is None:
                await wait_for_native(context.flow(draft), args.wait_seconds)
            else:
                await wait_for_native(context.flow(draft), args.wait_seconds, interaction=interaction)
    except (OnboardingClientError, OSError, ValueError, EOFError) as exc:
        from .native_hook_apply_result import ApplyError
        code = exc.code if isinstance(exc, OnboardingClientError) else "local_setup_unavailable"
        if args.action == "hooks" and isinstance(exc, ApplyError):
            print(json.dumps(dict(settings="failed", code=code, written=list(exc.written),
                readiness="not_verified"), sort_keys=True), flush=True)
        if interaction is not None:
            interaction.emit("error", code=code)
            raise SystemExit(1) from None
        print(f"Native setup failed: {code}. Existing setup state was not replaced.", file=sys.stderr)
        if code == "device_installation_unavailable":
            print("Enroll this device first: atellagent-cli setup device --platform-url <trusted HTTPS origin>",
                  file=sys.stderr)
        if code == "onboarding_native_setup_failed":
            print("This provisioning request failed permanently. Request a replacement connection after the cause is resolved; its original state remains saved.", file=sys.stderr)
        elif request_id is not None and args.action == "pickup":
            print(f"Retry exact preparation: atellagent-cli setup native pickup {request_id}", file=sys.stderr)
        elif request_id is not None and args.action not in {"hooks", "unselect"}:
            print(f"Continue: atellagent-cli setup native resume {request_id}\n"
                  f"If submission was interrupted: atellagent-cli setup native submit {request_id}", file=sys.stderr)
        raise SystemExit(1) from None
