# Atellagent Client

Atellagent Client is the local enforcement component of Agent Authority. It
extends Agent Authority into customer-operated runtimes, where it can request
decisions and enforce approved tool actions without exposing the runtime to
inbound Atellagent traffic. It is public client code: it implements documented
client contracts and local verification, not managed-service policy
implementation.

Agent Authority defines the policy and decision model across hosted workspaces
and participating external runtimes. Atellagent Client is the supported local
path for customer-owned Python agents, provider SDK calls, MCP peers, private
bridges, and documented Codex and Claude Code hook boundaries. Gemini CLI is
present as a preview adapter but is not a supported release host yet.
Coverage remains specific to the selected integration surface and the host
events it exposes.

## Start here

Install the core client for CPython 3.11:

```bash
pip install atellagent-client
```

Check the [installation trust prerequisites](COMPATIBILITY.md#installation-trust-prerequisites) before installing.

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
| `hook` | Codex or Claude Code needs local prompt/tool controls. | [External hosts](docs/HOST_HOOKS.md) |
| `provider_proxy` | You own a provider SDK call and its function tools. | [Provider sessions](integrations/providers/README.md) |
| `mcp_proxy` | A customer MCP peer needs compatibility handling. | [MCP proxies](proxy/README.md) |

`hook` and `provider_proxy` are integration surfaces, not additional policy
modes. The actual connected MCP tool boundary is distinct from the MCP
compatibility proxies; see the linked guides before selecting one.

For Codex, Claude Code or an existing local MCP server on macOS or Windows,
start with the [desktop app guide](docs/DESKTOP_APP.md). The
[local device service guide](docs/LOCAL_SERVICE.md) covers the same setup through
the CLI and running approved connections under your user account.
Setup uses browser authorization and explicit
organization selection, not downloaded identity configuration or copied tokens.
The Gemini CLI adapter is not release-supported yet.
Use `atellagent-cli discover` for a human-readable, read-only inventory of your Codex and
Claude Code settings; add `--project /path/to/project` to include one selected
project, or `--json` for redacted structured output. Discovery does not connect to
or approve MCP servers. Enroll the device once; Codex and Claude Code under the
same OS user share that device, but each connection requires its own approval
and owner consent. Device reporting works without an agent connection.
The local service reports a bounded current-user discovery
snapshot by default; the user can opt out per installation. See the
[background-service guide](docs/LOCAL_SERVICE.md) for the report contents and
opt-out behavior.

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

## Setup and operation

For native hosts, begin device setup and follow the reviewed request flow in the
[service guide](docs/LOCAL_SERVICE.md). For SDK and server/container deployments,
use [workload setup](connected/README.md): sign in through the browser link/code,
select an organization, submit a request and obtain administrator approval.

Keys stay local. Approved settings and credentials are delivered into private
managed state; runtime commands resume its exact request ID. Workloads operate
with their machine identity, while native agents use reviewed user delegation.
Ordinary runtime requests do not initiate browser sign-in.
Native delegation normally continues until revoked; explicit authorization
expiry, access removal and current policy still apply. Browser logout alone does
not end an approved native connection or workload.

For a portable container deployment, use the artifact-first image only by
digest; attended setup and runtime are separate invocations. See the
[Docker guide](docker/README.md).

## Public-contract boundary

Use the documented exports and guides only. Avoid underscored modules and
implementation modules not named as supported entry points. Public API
compatibility follows the [compatibility policy](COMPATIBILITY.md); report a
security issue through [SECURITY.md](SECURITY.md) without including credentials
or customer data.

## Protect your MCP server

[Enterprise MCP](docs/ENTERPRISE_MCP.md) adds a direct intake to the same connected MCP runtime and mounted handler. Your server authenticates callers; Atellagent authorizes each action before
execution. External MCP traffic is blocked by default. An administrator configures
**Allow external MCP traffic** during setup or in **Policies → Rules → Connection
controls**. The **External MCP traffic** shortcut in **Runtimes & Integrations**
edits the same setting.
