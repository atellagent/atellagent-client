Copyright (c) 2026 Atellagent, Inc. All rights reserved. See [LICENSE.md](http://license.md/).

# Building custom integrations

Atellagent customers extend the client by deploying their own adapter beside the
client and using a documented public contract. An adapter does not modify or
embed the client, and it may keep its own provider credentials and SDKs inside
the customer deployment.

## Choose a contract

| Need | Public contract |
| --- | --- |
| Customer agent runtime | `mount_agent_handler` from `atellagent_client.connected` |
| MCP peer compatibility | `atellagent-agent-proxy` or `atellagent-tool-proxy` |
| Model or filter provider | `ModelRuntimeHandler` or `FilterRuntimeHandler` from `atellagent_client.integrations.models` |
| Workflow runtime | `WorkflowParticipantHandler` from `atellagent_client.integrations.workflows` |
| Channel provider | `ChannelIngressAdapter` and/or `ChannelEgressAdapter`, registered with `ChannelAdapterRegistry` |
| Native provider function tools | `GovernedToolDescriptor` and `GovernedToolIngress` from `atellagent_client.integrations.providers.governed_tools` |
| Standalone local tool boundary | `RuntimeActionGate.from_local_manifest` from `atellagent_client.governance` |

The imports in this guide are the supported extension surface. Do not depend on
underscored names or implementation modules outside these public packages.

## Custom agent adapter

An agent adapter is an ordinary handler. It can call any customer-owned SDK,
receives the documented delivery payload, and returns a serializable result.
Consequential targets must honor the supplied idempotency key.

```python
import asyncio

from atellagent_client.connected import mount_agent_handler
from atellagent_client.sdk import ConnectedSDKRuntime
from atellagent_client.sdk.config import load_service_account_config_from_yaml


async def customer_agent(payload: dict) -> dict:
    # Call a customer-owned agent SDK here. Keep its credentials in the
    # customer environment, not in the Atellagent configuration.
    message = payload.get("input", "")
    return {"content": f"Handled: {message}", "metadata": {}}


async def run() -> None:
    runtime = ConnectedSDKRuntime(
        load_service_account_config_from_yaml("integration.yaml")
    )
    mount_agent_handler(
        runtime,
        customer_agent,
        consequential=False,
        target_idempotent=False,
    )
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()


asyncio.run(run())
```

For a consequential handler, set `consequential=True` and
`target_idempotent=True`, then pass the delivery idempotency key to the
customer-owned target that makes the external effect.

## Model, workflow, and channel adapters

Model/filter and workflow adapters implement the corresponding public Protocol
or base class, then are mounted on a `ConnectedSDKRuntime` with
`mount_model_handler`, `mount_filter_handler`, or `mount_workflow_handler`.
The type-specific request objects validate the supported public input and the
adapter returns the corresponding result object or mapping.

For channels, implement the ingress and/or egress Protocol, give the adapter a
`channel_type` and optional provider/adapter keys, and register it with
`ChannelAdapterRegistry`. The connected participant resolves the registered
adapter for each declared channel action.

## Provider-native function tools

For OpenAI, Google, or Anthropic function calling, describe each provider-visible
tool with `GovernedToolDescriptor`, then construct `GovernedToolIngress` with an
authenticated `AtellagentClient`. Pass the matching `tool_bridge` a provider SDK
callable. The provider receives only its native function schema; the customer
adapter submits returned function calls through the tool ingress.

## Standalone local enforcement

Use `RuntimeActionGate.from_local_manifest` for a customer-owned local tool
boundary. Pass only the documented action facts to `enforce`; `enforce` and
`observe` behavior is selected in the local manifest.

## Compatibility

Use only the imports and contracts named in this guide. Atellagent follows
semantic versioning for them: minor and patch releases preserve their documented
contract, while a major release may make a documented breaking change.
