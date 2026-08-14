# Provider sessions and native function tools

## When to use this package

Use this package when your customer application owns an OpenAI, Google, or
Anthropic provider SDK call and needs governed model turns and function tools.
It is a `provider_proxy` integration surface, not a hosted coding-agent
integration.

## Model governance

`GovernedProviderSession` has one explicit mode:

| Mode | Provider transport | Failure behavior |
| --- | --- | --- |
| `decision` | Your application invokes its native SDK only after admission. | A decision failure prevents that native turn. |
| `route` | The configured route performs the model call. | It never falls back to a native SDK call. |

`decision` retains the customer's provider subscription/transport behavior.
`route` uses the configured route and is not a subscription-preserving fallback.
Neither model mode rewrites responses in this client.

## Function-tool boundary

Use `GovernedToolDescriptor` and `GovernedToolIngress` to render native tool
schemas and submit returned provider tool calls through the documented effect
boundary. Provider-visible schemas do not expose target credentials or local
routing facts.

```python
from atellagent_client.integrations.providers import GovernedProviderSession, ModelGovernanceMode

session = GovernedProviderSession(governance=governance, mode=ModelGovernanceMode.DECISION)
```

Provider helper modules expose `tool_bridge` for Anthropic, Google, and OpenAI.
Use the matching SDK extra. Tool or model failures must not be retried through
an ungoverned provider/tool path. This package is not an MCP compatibility proxy
and does not make an external host hook observe full internal model requests.
