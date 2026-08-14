## License

Copyright (c) 2026 Atellagent, Inc. All rights reserved.

This repository's source code is free to use under the Atellagent Proprietary License —
see [LICENSE.md](LICENSE.md). This is a proprietary license, not an OSI-approved
open-source license.

Use of the hosted Atellagent cluster/runtime service is governed by
a separate commercial agreement and is not covered by the license
above.

# Atellagent Client

The Atellagent client supports two operating modes:

- **Local mode** runs bounded enforcement beside customer code with a local YAML
  manifest. It makes no connection to Atellagent infrastructure.
- **Connected mode** runs customer integrations as outbound connected
  participants. It does not open an Atellagent callback listener and does not
  require a customer FQDN, inbound firewall rule, or server TLS certificate.

Customer-operated adapters for third-party systems live under
`atellagent_client.integrations`; the outbound connected participant and bridge
live under `atellagent_client.connected`; local action enforcement lives under
`atellagent_client.governance`.

In connected mode, the gateway remains the authority. A participant
authenticates with its local service-account client certificate and OAuth
client, registers a durable lease, receives work over HTTP/2 long polling, and
validates every delivery capability against the gateway JWKS and its own
certificate public key before invoking the customer handler.

## Install

```bash
pip install atellagent-client
```

Install only the extras used by the integration:

```bash
pip install "atellagent-client[mcp]"
pip install "atellagent-client[openai]"
pip install "atellagent-client[google]"
pip install "atellagent-client[anthropic]"
pip install "atellagent-client[langgraph]"
pip install "atellagent-client[ollama]"
pip install "atellagent-client[huggingface-filter]"
pip install "atellagent-client[postgres]"
```

Python 3.11 is supported. Other Python versions are not part of the current
release test matrix.

## SDK language support

Python is the first supported Atellagent SDK language. We plan to expand native
SDK support to additional languages over time. New language SDKs will be
introduced incrementally based on supported integration capabilities.

To connect an unsupported provider or customer-owned runtime without modifying
the client, see [Building custom integrations](docs/BUILDING_CUSTOM_INTEGRATIONS.md).

## Connected mode: provision and enroll

Provisioning produces two artifacts:

- a non-secret YAML configuration containing immutable account, integration,
  endpoint, protocol, and packaging metadata;
- a short-lived, single-use enrollment token shown separately.

The private key is never provisioned by Atellagent. Run enrollment on the
customer runtime host:

```bash
atellagent-cli ./integration.yaml --enroll
```

The client generates the private key and CSR locally, proves possession through
the one-time token, validates the returned certificate chain, and atomically
saves exactly two runtime files: the private key and the client certificate
chain. The token is prompted without echo and is not written to configuration.

By default the files are saved beside the YAML in `certs/`. Runtime paths may be
provided with `ATELLAGENT_CERT_PATH` and `ATELLAGENT_KEY_PATH`. Keep the private
key owner-readable only. The participant process needs write access to the
credential directory for supervised atomic replacement during routine
rotation; no other process should have write access.

Gateway and authentication endpoints use publicly trusted server certificates,
so the client uses the operating-system trust store. There is no custom gateway
CA override, gateway client-CA file, or inbound server certificate in the
connected runtime contract.

Verify registration without accepting ongoing work:

```bash
atellagent-cli ./integration.yaml \
  --verify \
  --handler customer_runtime:handler \
  --target-idempotent
```

## Connected mode: run an SDK participant

An SDK participant mounts a handler directly in the customer process:

```python
import asyncio

from atellagent_client.connected import mount_agent_handler
from atellagent_client.sdk import ConnectedSDKRuntime
from atellagent_client.sdk.config import load_service_account_config_from_yaml


async def handle(payload: dict) -> dict:
    return {"content": "done", "metadata": {}}


async def main() -> None:
    config = load_service_account_config_from_yaml("integration.yaml")
    runtime = ConnectedSDKRuntime(config)
    mount_agent_handler(
        runtime,
        handle,
        consequential=False,
        target_idempotent=False,
    )
    try:
        await runtime.run_forever()
    finally:
        await runtime.stop()


asyncio.run(main())
```

Use `mount_model_handler`, `mount_filter_handler`,
`mount_workflow_handler`, or `mount_channel_registry` for the other integration
types. Runnable examples are under `examples/`.

## Connected mode: run a bridge

A bridge uses the same connected protocol but translates a delivery to a
customer-owned local or private target. It still opens no public listener:

```bash
atellagent-cli ./integration.yaml \
  --handler customer_bridge:handler \
  --target-idempotent
```

Consequential handlers must propagate `delivery.idempotency_key` to a target
that durably deduplicates it. `--target-idempotent` is an explicit assertion of
that property, not a client-side substitute for it.

Keep target credentials inside the bridge boundary. Prefer loopback, a Unix
socket, workload identity, or an explicitly enforced private-network policy.
If the target can also be called independently, Atellagent governance applies
only to calls that actually traverse the connected bridge; the target's own
authorization must protect every other path.

## MCP compatibility proxies

Use the optional proxies when a customer-owned MCP peer needs compatibility
handling. `atellagent-agent-proxy --config proxy.yaml` is a stdio MCP server
facade for an agent. `atellagent-tool-proxy --target URL --tool NAME
--action-key KEY` is an MCP client facade for a target. Install the matching
`mcp-agent-proxy` or `mcp-tool-proxy` extra when packaging either executable.

The agent proxy accepts the supported legacy initial negotiation revisions
(`2024-11-05`, `2025-03-26`, and `2025-06-18`) or modern `2026-07-28`
discovery. The tool proxy discovers the target revision on a new connection.
Invalid or ambiguous negotiation fails closed; no version is configured in a
client configuration file. Legacy connection state is bounded and local to the
proxy. Upgrade a peer to the modern revision when it supports it.

An agent proxy configuration names a generated client configuration and its
MCP-visible tools. Keep it customer-owned and provide only local file
paths and target bindings:

```yaml
client_config: ./client.yaml
source_agent: customer-agent
tools:
  - name: lookup_customer
    description: Look up an authorized customer record.
    input_schema:
      type: object
      properties:
        customer_id: {type: string}
    target_binding: granted-tool-binding
    target_tool_name: lookup_customer
```

## Provider-native tool use

OpenAI, Google, and Anthropic integrations use their native function/tool-use
formats. They are not MCP clients and never receive an Atellagent or external
MCP endpoint, MCP credential, or target binding.

Configure a provider-visible tool descriptor, then
send every returned provider tool call through the matching bridge:

```python
from atellagent_client.integrations.providers.anthropic import tool_bridge
from atellagent_client.integrations.providers.governed_tools import (
    GovernedToolDescriptor,
    GovernedToolIngress,
)
from atellagent_client.sdk.client import AtellagentClient
from atellagent_client.sdk.config import load_service_account_config_from_yaml
from atellagent_client.integrations.agents.control import ExternalAgentGovernance
from anthropic import AsyncAnthropic

service_account_config = load_service_account_config_from_yaml("integration.yaml")
descriptor = GovernedToolDescriptor(
    name="lookup_customer",
    description="Look up an authorized customer record.",
    input_schema={
        "type": "object",
        "properties": {"customer_id": {"type": "string"}},
        "required": ["customer_id"],
        "additionalProperties": False,
    },
    target_binding="granted-tool-binding",
    target_tool_name="customer_lookup",
)
bridge = tool_bridge(
    ingress=GovernedToolIngress(
        client=AtellagentClient(service_account_config),
        descriptors=[descriptor],
        provider="anthropic",
    )
)
anthropic_client = AsyncAnthropic()

# Each native provider request is wrapped by the governed session. The host
# owns the provider SDK and its credential; the session asks Atellagent for a
# decision before that request. It does not provide a tool-only loop.
from atellagent_client.integrations.providers import (
    GovernedProviderSession,
    ModelGovernanceMode,
)
from atellagent_client.protocol import ModelDecisionRequest

session = GovernedProviderSession(
    governance=ExternalAgentGovernance(service_account_config),
    mode=ModelGovernanceMode.DECISION,
)
messages = [{"role": "user", "content": "Look up customer 42."}]
turn = await session.native_turn(
    decision_request=ModelDecisionRequest(
        input_scope="full_model_request",
        messages=messages,
        provider="anthropic",
        model="claude-sonnet-4-5",
        tool_definitions=bridge.tool_definitions(),
        generation={"max_tokens": 1024},
    ),
    invoke=lambda: anthropic_client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        messages=messages,
        tools=bridge.tool_definitions(),
    ),
)
response = turn.provider_payload
```

Use `atellagent_client.integrations.providers.openai.tool_bridge` for OpenAI Responses API
function calls and `atellagent_client.integrations.providers.google.tool_bridge` for Google
GenAI function calls. Each bridge only normalizes a native tool request and
returns its governed result. After adding that result to the provider's next
request, wrap the next provider request with `session.native_turn` again.
Decision-mode transport never uses an Atellagent provider credential; route
mode instead uses `session.route_turn`, which returns the canonical routed
result and has no native-provider callback.

## PostgreSQL tools

`PostgresTools` is a deliberately bounded customer-hosted tool adapter. It
accepts parameterized `SELECT` queries and optional named write templates; it
never accepts write SQL from a model request. Keep the DSN in the customer
environment and use a least-privilege database role.

```python
from atellagent_client.integrations.tools import (
    PostgresTools,
    PostgresWriteOperation,
)

tools = PostgresTools.from_environment(
    write_operations=[
        PostgresWriteOperation(
            name="mark_reviewed",
            statement="UPDATE records SET reviewed = true WHERE id = %(id)s",
            description="Mark one authorized record as reviewed.",
        )
    ],
    max_rows=100,
)

result = await tools.query(
    "SELECT id, status FROM records WHERE id = %(id)s",
    {"id": "record-42"},
)
```

Publish only the `tools.tool_metadata()` schemas to a provider, and route the
corresponding calls through `GovernedToolIngress`. Do not expose the DSN,
query templates, or database connection to a provider-visible tool schema.

## Current limitations

- Ollama model invocations support non-streaming chat only. Streaming is
  coming soon.
- The governed OpenAI model-takeover helper does not currently support
  streaming, handoffs, or prompt configuration. Use the documented
  provider-native tool bridge for OpenAI function calls.

## Connected mode: configuration shape

The dashboard generates the complete connected-mode configuration. This
abridged SDK example shows its topology:

```yaml
gateway_mtls_url: https://gateway.example.com
oauth_token_url: https://auth.example.com/oidc/token
oauth_jwks_url: https://auth.example.com/oidc/jwks
client_id: service-account-client-id
service_account_id: immutable-service-account-id
integration_id: immutable-integration-id
tenant_id: immutable-tenant-id
placement: connected
protocol_version: v1
packaging: sdk
integration_type: agent
capabilities: [agent.process]
control_source: cluster_directive
deployment:
  type: sdk
```

Only `sdk` and `bridge` packaging are accepted. A participant establishes its
outbound connection using the configuration above.

Action identity uses one nested `identity_context` envelope. Its
`executor_identity`, `principal_identity`, `external_subject_identity`, and
`binding_identity` members carry the applicable identity facts for a governed
call.

The generated file is the configuration reference for immutable IDs and
versioned gateway paths. The customer-operated fields are:

- `packaging`: `sdk` or `bridge`; `deployment.type` must match.
- `integration_type`: `agent`, `mcp`, `channel`, `model`, `ml_filter`, or
  `workflow_runtime`.
- `control_source`: `cluster_directive`, or `local_manifest` when a connected
  MCP integration uses its locally selected control source.
- `local_guardrail_manifest_path`: required only for `local_manifest`; relative
  paths are resolved beside the generated YAML.
- `local_guardrail_mode`: the provisioned `enforce` or `observe` choice for a
  local manifest. The manifest must declare the same mode.
- `deployment`: the local handler or reviewed private-target shape used by a
  bridge.
- `timeout`: bounded request timeout in seconds.

Credential paths are supplied with `ATELLAGENT_CERT_PATH` and
`ATELLAGENT_KEY_PATH`. `ATELLAGENT_HANDLER` may replace `--handler`.
`ATELLAGENT_INSTANCE_KEY` supplies a stable process identity. The optional
`ATELLAGENT_CONTROL_SOURCE` and `ATELLAGENT_LOCAL_GUARDRAIL_MANIFEST`
environment variables make the same explicit source selection as the YAML;
`ATELLAGENT_LOCAL_GUARDRAIL_MODE` carries the corresponding mode. They never
enable fallback. Gateway and OAuth endpoint/path fields should be used exactly
as generated by the dashboard.

## Local mode: enforcement without a cluster connection

Local mode is a bounded, standalone control for customer-owned tool and MCP
boundaries. It reads a local manifest and evaluates each action in the customer
process; it does not provision, enroll, authenticate, or connect to the
Atellagent cluster.

Use `RuntimeActionGate.from_local_manifest` at the action boundary:

```python
from atellagent_client.governance import RuntimeActionGate


async def write_file() -> None:
    gate = RuntimeActionGate.from_local_manifest(
        "./local_guardrails.yaml",
        expected_mode="enforce",
    )
    await gate.enforce(
        action="file.write",
        integration_type="mcp",
        correlation_id="request-123",
        facts={
            "path": "/workspace/project/result.txt",
            "access": "write",
            "bytes": 128,
        },
    )
```

The same manifest can be selected for a connected runtime when a customer
wants local control at that boundary:

```yaml
integration_type: mcp
control_source: local_manifest
local_guardrail_manifest_path: ./local_guardrails.yaml
local_guardrail_mode: enforce
```

The `v1` manifest has exactly `schema_version`, `mode`, and a non-empty
`actions` mapping:

```yaml
schema_version: v1
mode: enforce
actions:
  file.read:
    readable_roots: [/workspace/project]
    max_bytes: 262144
  file.write:
    writable_roots: [/workspace/project]
    max_bytes: 262144
  file.patch:
    max_patch_hunks: 80
  checks.run:
    allowed_profiles: [test, lint]
  git.push:
    allowed_git_targets: [origin/main]
```

Rules can use only `readable_roots`, `writable_roots`, `max_bytes`,
`max_results`, `max_patch_hunks`, `allowed_profiles`, and
`allowed_git_targets`. The adapter projects only the corresponding documented
facts (`path`, `access`, `bytes`, `results`, `patch_hunks`, `profile`, and
`git_target`). In `enforce` mode, an unlisted action, missing required fact,
out-of-root path, malformed manifest, or unavailable manifest fails closed.
In `observe` mode, violations are allowed and reported as `would_enforce`.
Every local decision emits a metadata-only `atellagent_client.pep` log with
source, mode, decision, action, correlation ID, coverage, and reason; action
arguments are not logged.

For connected use, the dashboard writes the selected Enforce or Observe choice
to `local_guardrail_mode`; startup requires it to match the manifest. A
connected participant still authenticates and receives cluster deliveries, but
the selected local action boundary evaluates the manifest above. Local mode
does not make that connection.

## Certificate rotation

Routine client-certificate rotation is automatic. The cluster tracks the
certificate due date and deadline, alerts administrators, and durably queues a
`certificate.rotate` control delivery. The participant core consumes it before
customer code, pauses receive, drains active deliveries, creates a new private
key and CSR locally, submits that CSR under the current mTLS/OAuth identity,
validates and stages the issued chain, activates a zero-overlap identity
cutover, reloads its TLS state, and reconnects. The private key never leaves the
runtime host, and no dashboard token is required for routine rotation.
Each heartbeat independently reads the installed certificate fingerprint and
expiry; the cluster rejects a mismatch and uses a due heartbeat as an
idempotent scheduling backstop for the durable control delivery.

Keep the participant online during its rotation window and make the credential
directory writable by the runtime process. Monitor dashboard due/deadline and
online status. If the participant is offline, its durable control delivery
waits until the deadline. If the issuer is temporarily unavailable, issuance
stays bounded by that deadline and the participant retries status checks with
capped backoff.

If the old identity is expired, revoked, or unavailable, a tenant administrator
must create a new short-lived, single-use recovery enrollment. On the runtime
host run:

```bash
atellagent-cli ./integration.yaml --enroll --replace-credentials
```

That recovery path is intentionally separate from routine rotation. Treat a
post-activation local file/reconnect failure as recovery enrollment: the old
certificate is already rejected and is never restored as compatibility trust.

## CLI reference

`atellagent-cli CONFIG` runs the sole connected participant described by the
generated YAML. Runtime options are `--handler module:attribute`,
`--target-idempotent`, `--verify`, and `-v`/`-vv`. `--verify` registers and
then drains without accepting ongoing work. Use the dedicated MCP compatibility
proxy commands for MCP peers.

For a supported external-agent hook, run the enrolled control boundary on a
user-owned Unix socket:

```bash
atellagent-cli ./hook-control.yaml --hook-control-socket "$XDG_RUNTIME_DIR/atellagent/control.sock"
```

That YAML must describe a connected `agent` boundary with exactly the
provisioned `agent.control` capability and `boundary_identity_only` identity
mode. The service exposes only turn-entry model decisions, tool preflight and
postflight, and health discovery. It opens no TCP listener; the socket and its
parent directory are owner-only, and hook processes hold no Atellagent
credential. A missing service, expired directive, control timeout, or an
obligation the hook protocol cannot fulfill is a deny—not a local allow.
For Claude Code and Codex command-hook installation, coverage limits, and
managed deployment templates, see [External coding-host hooks](docs/HOST_HOOKS.md).

`--enroll` selects enrollment instead of runtime execution and prompts for the
single-use token without echo. `--cert-path` and `--key-path` override its two
output paths. `--replace-credentials` is permitted only with `--enroll` and is
reserved for administrator-authorized recovery. Runtime handler options cannot
be combined with enrollment.

## Lifecycle and security behavior

The participant owns registration, long-poll receive, acknowledgement, lease
renewal, result commit, heartbeat, graceful drain, and deregistration. A `401`
invalidates the cached OAuth token and retries once. Revoked or expired
credentials still fail closed.

Delivery capabilities are issuer-, audience-, tenant-, integration-, operation-,
message-, lease-, time-, and certificate-public-key bound. The customer handler
receives a secret-free delivery DTO, preventing routine envelope, telemetry, and
logging leaks. Run only trusted handler code in the participant process; deploy
untrusted extensions behind a separate authenticated process boundary.

Do not log enrollment tokens, OAuth tokens, private keys, delivery capabilities,
or customer payloads. See `SECURITY.md` for reporting and trust-boundary details.
