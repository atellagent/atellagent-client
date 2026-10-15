"""Open a route facade from a saved, approved native request, never YAML."""

from uuid import UUID

from atellagent_client.onboarding.installation_paths import installation_directory
from atellagent_client.onboarding.native_setup_context import NativeSetupContext
from atellagent_client.onboarding.native_runtime_config import open_native_runtime


def add_request_option(parser):
    parser.add_argument("--request-id", type=UUID, required=True,
                        help="Saved approved native request for this installation")


async def run_facade(args, runtime_type, label, token_loader):
    host, separator, port = args.listen.rpartition(":")
    if not separator or not host:
        raise ValueError("--listen must use HOST:PORT")
    port = int(port)
    if host not in {"127.0.0.1", "::1"} or not 0 <= port <= 65535:
        raise ValueError("route facade requires a loopback host and port between 0 and 65535")
    token = token_loader(args.token_file)
    async with await NativeSetupContext.open(installation_directory()) as context:
        setup = await open_native_runtime(context, args.request_id)
        runtime = runtime_type(setup.config, native_binding=setup.binding,
                               capability_token=token, host=host, port=port)
        try:
            await runtime.start()
            address = runtime.address
            print(f"{label} route facade listening on http://{address[0]}:{address[1]}", flush=True)
            await runtime.run_forever()
        finally:
            await runtime.stop()
