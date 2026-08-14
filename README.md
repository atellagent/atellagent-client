# Atellagent Client

The Atellagent client lets customer-operated code request governance decisions
and enforce approved tool actions without exposing a customer runtime to inbound
Atellagent traffic. It is public client code: it implements documented client
contracts and local verification, not managed-service policy implementation.

## Start here

Install the core client for CPython 3.11:

```bash
pip install atellagent-client
```

Install only the provider extra used by a customer adapter, for example
`atellagent-client[openai]`, `atellagent-client[google]`, or
`atellagent-client[anthropic]`. See the [documentation index](docs/README.md)
for every supported extra and integration path.

### Choose placement

| Placement | Use it when | Start with |
| --- | --- | --- |
| `hosted` | Atellagent operates the runtime. | Managed product documentation |
| `connected` | Customer code needs outbound-only participation. | [Connected runtime](connected/README.md) |
| `external_resource` | A customer-owned target is reached by a customer adapter or bridge. | [Custom integrations](docs/BUILDING_CUSTOM_INTEGRATIONS.md) |

This package documents the `connected` and customer-owned adapter paths. It
does not alter hosted runtime architecture.

### Choose an integration surface

| Surface | Use it when | Guide |
| --- | --- | --- |
| `sdk` | You own the Python agent process. | [SDK](sdk/README.md) |
| `bridge` | An enrolled participant dispatches to a private target. | [Connected runtime](connected/README.md) |
| `hook` | Claude Code or Codex needs local prompt/tool controls. | [External hosts](docs/HOST_HOOKS.md) |
| `provider_proxy` | You own a provider SDK call and its function tools. | [Provider sessions](integrations/providers/README.md) |
| `mcp_proxy` | A customer MCP peer needs compatibility handling. | [MCP proxies](proxy/README.md) |

`hook` and `provider_proxy` are integration surfaces, not additional policy
modes. The actual connected MCP tool boundary is distinct from the MCP
compatibility proxies; see the linked guides before selecting one.

### Choose model governance

| Mode | Provider transport | Coverage |
| --- | --- | --- |
| `decision` | The host or customer adapter keeps its native provider call. | A decision before the documented request/checkpoint. |
| `route` | Atellagent performs the configured provider route. | The routed model request. |

Neither mode silently falls back to the other. A host submitted-prompt hook is
`turn_entry` coverage, not a claim to observe every subsequent model
request. See [provider sessions](integrations/providers/README.md) and
[external hosts](docs/HOST_HOOKS.md).

### Choose identity

| Mode | Meaning |
| --- | --- |
| `boundary_identity_only` | Policy uses the enrolled boundary identity. |
| `federated_agent_identity` | A custom SDK/adapter supplies separately verifiable external identity evidence. |

The mode is provisioned with the connected boundary and is not a local
configuration switch. Standard Claude Code and Codex hooks use
`boundary_identity_only`.

## Enrollment and operation

Provisioning supplies a non-secret YAML configuration and a single-use
enrollment token separately. Enroll on the customer runtime host:

```bash
atellagent-cli ./integration.yaml --enroll
```

The client creates the private key locally and prompts for the token without
echoing or storing it. Keep the generated key/certificate owner-readable and
the credential directory writable by the participant for routine rotation.

For a portable container deployment, use the artifact-first image only by
digest; enrollment and runtime are separate invocations. See the
[Docker guide](docker/README.md).

## Public-contract boundary

Use the documented exports and guides only. Avoid underscored modules and
implementation modules not named as supported entry points. Public API
compatibility follows the [compatibility policy](COMPATIBILITY.md); report a
security issue through [SECURITY.md](SECURITY.md) without including credentials
or customer data.
