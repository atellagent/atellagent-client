# Connected runtime

## Purpose and placement

Use a connected participant when customer code must operate outbound-only. It
does not expose an Atellagent callback listener or require a customer FQDN.
Use a `ConnectedBridge` when the participant dispatches to a customer-owned
private target instead of executing the handler itself.

## Authentication and packaging

Provision a connected configuration, then enroll it once on the runtime host:

```bash
atellagent-cli ./integration.yaml --enroll
```

The participant uses its enrolled local key/certificate; customer target or
provider credentials stay inside the customer handler or bridge. Install the
core client plus the optional extra required by the target protocol.

## Public entry points and failure behavior

Use `ConnectedParticipant`, `ConnectedBridge`, `ConnectedDelivery`, and the
`mount_*` helpers exported from `atellagent_client.connected`. A handler must
return a serializable result. Consequential private targets must honor the
delivery idempotency key; setting `target_idempotent=True` is an explicit
customer assertion, not local deduplication.

Invalid configuration, delivery capability validation failures, and an
unavailable connected control path fail rather than dispatching an ungoverned
replacement call. No inbound listener is created.

```python
from atellagent_client.connected import mount_agent_handler
from atellagent_client.sdk import ConnectedSDKRuntime, load_service_account_config_from_yaml

runtime = ConnectedSDKRuntime(load_service_account_config_from_yaml("integration.yaml"))
mount_agent_handler(runtime, customer_handler, consequential=False, target_idempotent=False)
```

Use `mount_model_handler`, `mount_filter_handler`, `mount_mcp_handler`,
`mount_workflow_handler`, or `mount_channel_registry` only for the matching
connected integration type. See [custom integrations](../docs/BUILDING_CUSTOM_INTEGRATIONS.md)
for minimal runnable patterns.

## Exclusions and diagnostics

This is not an MCP compatibility proxy, an external-host hook, or a provider
route facade. It does not grant identity to a caller-supplied principal. Use
the participant's sanitized logs and correlation identifiers for diagnostics;
do not include private keys, certificates, enrollment tokens, or payloads in a
support request.
