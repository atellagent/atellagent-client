# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""One CLI for enrollment and outbound connected-runtime participation."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import getpass
import importlib
import inspect
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from atellagent_client.integrations.channels.registry import ChannelAdapterRegistry
from atellagent_client.connected import (
    ConnectedBridge,
    LocalMCPClient,
    mount_agent_handler,
    mount_channel_registry,
    mount_filter_handler,
    mount_mcp_handler,
    mount_model_handler,
    mount_workflow_handler,
)
from atellagent_client.sdk import ConnectedSDKRuntime
from atellagent_client.sdk.config import (
    BridgeDeploymentConfig,
    ServiceAccountConfig,
    load_service_account_config_from_yaml,
)
from atellagent_client.sdk.enrollment import (
    CertificateEnrollmentError,
    enroll_service_account_certificate,
)


def _die(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enroll or run an outbound Atellagent connected participant."
    )
    parser.add_argument("config", help="Path to the generated connected YAML")
    parser.add_argument(
        "--enroll",
        action="store_true",
        help="Generate one local clientAuth key/CSR and enroll the certificate",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Register, verify the connected path, then drain and exit",
    )
    parser.add_argument(
        "--replace-credentials",
        action="store_true",
        help="Atomically replace the local client certificate/key during rotation",
    )
    parser.add_argument("--cert-path", help="Enrollment certificate-chain output path")
    parser.add_argument("--key-path", help="Enrollment private-key output path")
    parser.add_argument(
        "--handler",
        default=None,
        help="Customer handler or adapter in module:attribute form",
    )
    parser.add_argument(
        "--mcp-manifest",
        help=(
            "Optional reviewed MCP 2026-07-28 manifest JSON/YAML; local stateless "
            "MCP bridges otherwise discover tools"
        ),
    )
    parser.add_argument(
        "--target-idempotent",
        action="store_true",
        help="Assert the mounted target durably honors each delivery idempotency key",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    return parser.parse_args()


def _load_object(reference: str) -> Any:
    module_name, separator, attribute_path = str(reference or "").strip().partition(":")
    if not module_name or not separator or not attribute_path:
        _die("--handler must use module:attribute notation")
    try:
        value: Any = importlib.import_module(module_name)
        for attribute in attribute_path.split("."):
            value = getattr(value, attribute)
        if inspect.isclass(value):
            value = value()
    except (ImportError, AttributeError, TypeError) as exc:
        _die(f"Unable to load handler '{reference}': {exc}")
    return value


def _load_manifest(path: Optional[str]) -> Optional[dict[str, Any]]:
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
        value = yaml.safe_load(text) if not path.lower().endswith(".json") else json.loads(text)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        _die(f"Unable to read MCP manifest: {exc}")
    if not isinstance(value, Mapping):
        _die("MCP manifest must be an object")
    return dict(value)


def _mount_handler(
    participant: Any,
    config: ServiceAccountConfig,
    handler: Any,
    *,
    target_idempotent: bool,
) -> None:
    integration_type = str(config.integration_type)
    if integration_type == "agent":
        if not callable(handler):
            _die("Agent participant handler must be callable")
        mount_agent_handler(
            participant,
            handler,
            consequential=True,
            target_idempotent=target_idempotent,
        )
    elif integration_type == "model":
        mount_model_handler(participant, handler, target_idempotent=target_idempotent)
    elif integration_type == "ml_filter":
        mount_filter_handler(participant, handler, target_idempotent=target_idempotent)
    elif integration_type == "workflow_runtime":
        mount_workflow_handler(
            participant,
            handler,
            target_idempotent=target_idempotent,
        )
    elif integration_type == "channel":
        if not isinstance(handler, ChannelAdapterRegistry):
            _die("Channel participant handler must be a ChannelAdapterRegistry")
        mount_channel_registry(
            participant,
            handler,
            target_idempotent=target_idempotent,
        )
    elif integration_type == "mcp":
        if not callable(handler):
            _die("MCP participant handler must accept (request, idempotency_key)")
        mount_mcp_handler(
            participant,
            handler,
            consequential=True,
            target_idempotent=target_idempotent,
        )
    else:
        _die(f"Unsupported integration type: {integration_type}")


async def _enroll(args: argparse.Namespace) -> None:
    token = (
        getpass.getpass("One-time enrollment token: ")
        if sys.stdin.isatty()
        else sys.stdin.readline().strip()
    )
    if not token:
        _die("A one-time enrollment token is required")
    try:
        result = await enroll_service_account_certificate(
            config_path=args.config,
            enrollment_token=token,
            certificate_path=args.cert_path,
            private_key_path=args.key_path,
            replace=args.replace_credentials,
        )
    except CertificateEnrollmentError as exc:
        _die(str(exc))
    finally:
        token = ""
    print("Certificate enrollment succeeded.")
    print(f"  certificate chain: {result.certificate_path}")
    print(f"  private key: {result.private_key_path}")
    print(f"  expires: {result.certificate_expires_at.isoformat()}")
    print(f"Verify with: atellagent-cli {args.config} --verify --handler module:attribute --target-idempotent")


async def _run(args: argparse.Namespace) -> None:
    config = load_service_account_config_from_yaml(args.config)
    if not args.target_idempotent:
        _die(
            "Connected consequential handlers require --target-idempotent; the target "
            "must durably honor delivery.idempotency_key"
        )

    local_mcp: Optional[LocalMCPClient] = None
    manifest = _load_manifest(args.mcp_manifest)
    handler: Any
    if (
        config.packaging == "bridge"
        and config.integration_type == "mcp"
        and isinstance(config.deployment, BridgeDeploymentConfig)
        and config.deployment.target_transport in {"stdio", "http"}
    ):
        local_mcp = LocalMCPClient(config.deployment)
        if manifest is None:
            manifest = await local_mcp.manifest()
        handler = local_mcp.invoke
    else:
        handler_reference = args.handler or os.getenv("ATELLAGENT_HANDLER")
        if not handler_reference:
            _die("This participant requires --handler module:attribute")
        handler = _load_object(handler_reference)
    if config.integration_type == "mcp" and manifest is None:
        _die("MCP handler participants require --mcp-manifest")

    participant_type = ConnectedSDKRuntime if config.packaging == "sdk" else ConnectedBridge
    participant = participant_type(config, mcp_manifest=manifest)
    _mount_handler(
        participant,
        config,
        handler,
        target_idempotent=args.target_idempotent,
    )
    try:
        await participant.start()
        print(f"Connected participant registered: {participant.instance_id}")
        if not args.verify:
            await participant.run_forever()
    finally:
        await participant.stop()
        if local_mcp is not None:
            await local_mcp.close()


async def _async_main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.enroll:
        if args.verify or args.handler or args.mcp_manifest or args.target_idempotent:
            _die("Runtime options cannot be combined with --enroll")
        await _enroll(args)
        return
    if args.replace_credentials or args.cert_path or args.key_path:
        _die("Credential output options require --enroll")
    await _run(args)


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(_async_main())


if __name__ == "__main__":
    main()
