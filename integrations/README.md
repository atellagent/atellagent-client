# Integration adapters

These packages are customer-operated adapters. Choose the narrow package that
matches the integration you own:

| Need | Guide |
| --- | --- |
| Connected agent boundary or external host hook control | [Agents](agents/README.md) |
| Native OpenAI, Google, or Anthropic provider sessions/tools | [Providers](providers/README.md) |
| Customer-owned local PostgreSQL tools | [Tools](tools/README.md) |
| Connected model/filter implementations | [Models](models/README.md) |
| Channel ingress/egress | [Channels](channels/README.md) |
| Workflow participant | [Workflows](workflows/README.md) |

An adapter owns only customer-side protocol translation. Provider credentials,
target credentials, and customer code remain in the customer deployment. No
adapter is a substitute for authorization at an independently reachable effect
boundary.
