"""Reusable SDK/bridge workload lifecycle with paired resource cleanup."""

import importlib
import inspect
import json
from contextlib import asynccontextmanager
from pathlib import Path
import yaml
from atellagent_client.connected import (ConnectedBridge, LocalMCPClient, mount_agent_handler,
    mount_channel_registry, mount_filter_handler, mount_mcp_handler, mount_model_handler, mount_workflow_handler)
from atellagent_client.integrations.channels.registry import ChannelAdapterRegistry
from atellagent_client.sdk import ConnectedSDKRuntime


def load_handler(reference):
    module, _, attribute = reference.partition(":")
    value = importlib.import_module(module)
    for name in attribute.split("."):
        value = getattr(value, name)
    return value() if inspect.isclass(value) else value


def load_manifest(path):
    if path is None:
        return None
    text = Path(path).read_text(encoding="utf-8")
    value = json.loads(text) if path.lower().endswith(".json") else yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError("MCP manifest must be an object")
    return value


def mount_handler(participant, config, handler, *, target_idempotent):
    kind = config.integration_type
    if kind in {"agent", "mcp"}:
        if not callable(handler): raise ValueError("Agent/MCP handler must be callable")
        mount = mount_agent_handler if kind == "agent" else mount_mcp_handler
        mount(participant, handler, consequential=True, target_idempotent=target_idempotent)
    elif kind == "channel":
        if not isinstance(handler, ChannelAdapterRegistry): raise ValueError("Channel handler must be a ChannelAdapterRegistry")
        mount_channel_registry(participant, handler, target_idempotent=target_idempotent)
    else:
        mounts = {"model": mount_model_handler, "ml_filter": mount_filter_handler, "workflow_runtime": mount_workflow_handler}
        if kind not in mounts: raise ValueError("Unsupported workload handler profile")
        mounts[kind](participant, handler, target_idempotent=target_idempotent)


@asynccontextmanager
async def open_participant(config, inputs, *, handler=None, mcp_manifest=None,
                           device_proof_provider=None, device_id=None):
    if not inputs.target_idempotent:
        raise ValueError("The customer target must durably honor delivery idempotency keys; configure --target-idempotent")
    participant, local_mcp = None, None
    try:
        manifest = mcp_manifest if mcp_manifest is not None else load_manifest(inputs.mcp_manifest_path)
        if manifest is not None and not isinstance(manifest, dict):
            raise ValueError("MCP manifest must be an object")
        if config.packaging == "bridge" and config.integration_type == "mcp" and config.deployment.target_transport in {"stdio", "http"}:
            if handler is not None:
                raise ValueError("A bridge target cannot also use a caller-supplied handler")
            local_mcp = LocalMCPClient(config.deployment)
            if manifest is None: manifest = await local_mcp.manifest()
            handler = local_mcp.invoke
        else:
            if handler is None:
                if inputs.handler is None: raise ValueError("Supply a customer handler or configure its module:attribute reference")
                handler = load_handler(inputs.handler)
        if config.integration_type == "mcp" and manifest is None:
            raise ValueError("MCP handler participants require a local MCP manifest")
        participant_type = ConnectedSDKRuntime if config.packaging == "sdk" else ConnectedBridge
        participant = participant_type(config, mcp_manifest=manifest)
        if (device_proof_provider is None) != (device_id is None):
            raise ValueError("Exact device proof and runtime association required")
        if device_proof_provider is not None:
            participant.set_device_proof_provider(device_proof_provider)
            participant.set_device_instance_key(device_id)
        mount_handler(participant, config, handler, target_idempotent=inputs.target_idempotent)
        await participant.start()
        yield participant
    finally:
        try:
            if participant is not None: await participant.stop()
        finally:
            if local_mcp is not None: await local_mcp.close()
