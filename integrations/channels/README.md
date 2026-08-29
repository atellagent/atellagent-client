# Channel adapters

## Purpose

Use channel adapters for customer-owned channel ingress and egress. Register
public `ChannelIngressAdapter` and `ChannelEgressAdapter` implementations in a
`ChannelAdapterRegistry`; `SlackChannelAdapter` is an optional provider adapter.

## Packaging, secrets, and entry points

Install the client and any provider-specific dependency in the customer
deployment. Channel tokens remain in customer secret storage. Mount the
registry through `mount_channel_registry` on the matching connected runtime.

```python
from atellagent_client.integrations.channels import ChannelAdapterRegistry

registry = ChannelAdapterRegistry()
```

Channel adapter validation and delivery failures are returned through the
documented contract. A channel adapter is not a workflow requirement, a model
proxy, or authorization for a channel endpoint callable outside the adapter.
