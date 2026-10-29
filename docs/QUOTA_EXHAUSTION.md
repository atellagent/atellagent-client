# Governed-action allowance

An organization administrator chooses what eligible customer-operated clients
should do after the governed-action allowance is exhausted. The control is in
**Policies → Rules → Integration controls → Organization**:

- **Stop governed actions** is the default. New governed work stops locally with
  a quota message until the allowance becomes available again.
- **Continue without central policy checks** lets supporting clients continue
  eligible work locally. Central policy checks and per-action cloud audit pause.
  Only independent restrictions already enforced by the local runtime remain.

This is an explicit reduction in protection. Central approval, action-history,
content and other policy requirements cannot be enforced while central checks
are paused. Actions executed this way produce no central action receipt or
successful cloud audit record, and are not queued for later audit upload.

Observe remains advisory. Exhaustion does not turn an Observe connection into
an enforcing connection. Atellagent-hosted execution, requests routed through
Atellagent to a provider/tool, and other independent allowances remain subject
to their limits. Local continuation requires the execution owner to be on the
customer side: a supported native hook, a decision-mode model wrapper using the
customer's provider transport, or the Enterprise MCP resource intake.
Model-wrapper passthrough uses a `boundary_identity_only` source connection.
Federated-source agent calls still require central caller authentication and
cannot use passthrough. This does not change an MCP vendor's own OAuth/JWT flow.

## Authority and recovery

The common client quota helper accepts only signed, scoped server instructions.
Each instruction is bound to the organization, integration, service account,
local surface, allowance period and settings revisions. Native instructions
also bind the current human grant. Instructions last at most 60 seconds and
cannot outlive that grant's expiry. They are separate from action directives.

A generic rate-limit response, policy refusal, held approval, authentication
failure, timeout, invalid signature or expired instruction never authorizes
local continuation. Native hooks and resource-side MCP callers retain their
existing authentication, transport and local restriction checks. An MCP vendor
still authenticates each caller using its own OAuth/JWT flow before invoking
`handle_mcp_request`; quota state does not authenticate those callers.

While a verified exhausted state is current, repeated actions do not send
per-action policy or audit requests. Existing centrally admitted outcomes drain
normally. The execution owner remains responsible for local idempotency and
for its own resource/transport rules during passthrough.

The client shares one bounded recovery request among concurrent calls and uses
backoff after failures. Setting changes, allowance increases and period resets
reach clients within the 60-second authority bound. Expired passthrough stops
until fresh authority is verified. Existing heartbeat and revocation traffic
continues; this feature removes repeated action traffic, not all background
connection traffic.

Applications using `ExternalAgentGovernance` can recheck immediately:

```python
status = await governance.refresh_quota_state("local_model")
```

For an owner-private native hook controller, the existing local RPC client can
request an immediate refresh without exposing credentials:

```python
from atellagent_client.integrations.agents.hook_control import HookControlClient

status = await HookControlClient(socket_path).call("quota.refresh", {})
```

Resource-side MCP applications can call `await participant.refresh_quota_state()`.

These return diagnostics, not an action permit. Normal governed admission
resumes when the allowance is available. A model result produced during local
continuation is advisory and explicitly unevaluated, with no central decision
ID. Supporting clients must use this shared helper rather than treating every
HTTP 429 as permission to continue.
