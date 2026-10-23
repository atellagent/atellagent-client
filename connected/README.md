# Connected runtime

## Purpose and placement

Use a connected participant when customer code must operate outbound-only. It
does not expose an Atellagent callback listener or require a customer FQDN.
Use a `ConnectedBridge` when the participant dispatches to a customer-owned
private target instead of executing the handler itself.

## Authentication and packaging

Request a workload on its runtime host using browser sign-in. The link/code can
be completed on another device; choose the organization explicitly. For example,
a customer model handler using SDK packaging:

```bash
atellagent-cli workload request \
  --state-directory /absolute/private/workload-state \
  --platform-url https://atellagent.com \
  --name "Customer model" --type model --capability model.inference \
  --handler customer.model:Handler --target-idempotent
```

The command prints the saved request ID. Your administrator approves it in
Discovery. If approval is still pending when the bounded wait ends, continue
the same request without signing in again:

```bash
atellagent-cli workload resume <request-id> --state-directory /absolute/private/workload-state
atellagent-cli workload verify <request-id> --state-directory /absolute/private/workload-state
atellagent-cli workload run <request-id> --state-directory /absolute/private/workload-state
```

`verify` registers and drains the participant; it is not an action-policy test.
`run` remains in the foreground until stopped. If initial submission failed
before the server accepted the workload request, use `workload submit` with the
saved ID and state directory to explicitly sign in and resubmit the retained
setup—not to generate new credentials.

The participant uses its approved local key/certificate and machine
authentication. Ordinary calls do not require a continuing browser session;
current revocation, integration state and policies still apply. Customer target
or provider credentials stay with the customer handler or bridge. Install the
core client plus the optional extra required by the target protocol.

Use `workload request --help` for supported profiles and SDK/bridge packaging.
`workload configure <request-id> --state-directory …` replaces local launch
settings such as the handler, bridge target or manifest, without changing the
approved identity. Include all required local settings and the idempotency
assertion when configuring. See `workload configure --help` for target options.
Native coding-host controllers use `setup native`, not workload setup.

For administrator-prepared settings, Python callers with a saved workload setup
can use `await workload_context.preparations(setup)` to review a page and
`await workload_context.pickup_preparation(setup, preparation)` to submit the
selected item. Pickup retains the same keys and settings and saves the request
receipt. It does not grant approval or start the workload; complete the normal
approval and setup steps before running it.

The same flow is available from a terminal:

```bash
atellagent-cli workload bootstrap --platform-url https://atellagent.com --name "Runtime host" --state-directory /absolute/private/workload-state
atellagent-cli workload preparations <bootstrap-id> --state-directory /absolute/private/workload-state --json
atellagent-cli workload pickup <bootstrap-id> <preparation-id> --state-directory /absolute/private/workload-state
```

Complete the browser link/code and organization selection during bootstrap.
An administrator must target prepared settings to that bootstrap. Listing and
pickup need no new sign-in. Use `--after <cursor>` for the next page; pickup
selects from that page. If bootstrap submission fails,
`workload bootstrap-submit <bootstrap-id> --state-directory …` signs in and resubmits the saved keys.
After pickup, use its request ID to `configure` local launch settings, then
`resume` setup after approval. Keep the private state directory between commands.

## Public entry points and failure behavior

Use `ConnectedParticipant`, `ConnectedBridge`, `ConnectedDelivery`, and the
`mount_*` helpers exported from `atellagent_client.connected`. A handler must
return a serializable result. Consequential private targets must honor the
delivery idempotency key; setting `target_idempotent=True` is an explicit
customer assertion, not local deduplication.

Local MCP bridges additionally validate the target's negotiated protocol and
advertised delivery contract before registering. Targets must support protocol
`2026-07-28` and advertise this discovery capability:

```json
{"experimental": {"atellagent/durableIdempotency": {
  "key": "atellagent/idempotencyKey", "durable": true
}}}
```

The target must actually durably deduplicate consequential calls using that
`_meta` key; declaring the capability is a contract, not an implementation of
deduplication. An arbitrary MCP server without this support is not a supported
consequential bridge target. Stdio and loopback streamable HTTP are supported;
SSE and remote HTTP targets are not.

For device-associated MCP setup, use `DeviceMCPContext` from the public SDK or
`atellagent-cli setup mcp --help`. A selected discovered target is read locally;
commands, endpoints and credentials are not added to discovery reports. Local
start requires review of its launch settings; stop removes the local selection
without revoking the server-side service account. A foreground `setup mcp run`
does not need the background service or desktop app. The shared service runs
selected bridges alongside agent controllers and discovery reporting.
Only one local participant can own a device-bound MCP integration at a time;
stop its background selection before running the same request in the foreground.
Other integration requests and standalone scaled receivers remain independent.

For administrator-prepared device MCP setup, reserve keys with
`setup mcp bootstrap --name "Local MCP connection"`. Use `setup mcp bootstraps --json`
to find retained reservations after reopening the app or terminal. An administrator
assigns settings to that reservation; `setup mcp preparations <session-id> --json`
lists them. Pick the exact assignment and local discovered target:

```sh
atellagent-cli setup mcp pickup <session-id> <assignment-id> --host codex --discovered-name example
```

Pickup saves the local target and submits the integration for approval; it does not
launch it. One administrator integration review covers its key reservation as well.
If reservation submission was interrupted, `setup mcp bootstrap --session-id <session-id>`
resubmits the same keys. Continue a linked request rather than creating a new one.
The desktop apps expose these actions under “Administrator-prepared connection.”
SDK callers use `DeviceMCPContext.bootstrap_setups()`, `load_bootstrap()`,
`preparations()` and `pickup_preparation()`; credential references remain local.

Device-associated MCP servers can also be added without discovery. Use
`await context.prepare_manual_target(management, display_name="Local tools",
url="http://127.0.0.1:8787/mcp")` on a `DeviceMCPContext`, or supply `command`
and an `arguments` list for stdio. This uses the same administrator approval,
credential activation and local launch review as a discovered target. The CLI
equivalent is `atellagent-cli setup mcp request --name "Local tools"
--target-url http://127.0.0.1:8787/mcp`; stdio uses `--target-command` and repeatable
`--target-arg=value`. These commands do not start the server.

Local credential references contain names, never credential values. For stdio,
pass `--credential-env TARGET=HOST_ENVIRONMENT_NAME` for each declared target
variable whose value is held in the host environment. For loopback HTTP, use
`--http-token-env HOST_ENVIRONMENT_NAME` and optionally `--http-auth-header`.
The HTTP environment variable contains the complete header value (including
`Bearer ` when required). Variables must be available to the foreground process
or background service that runs the bridge. Declared credential values are not
copied from host configuration or uploaded with connection requests.

Invalid configuration, delivery capability validation failures, and an
unavailable connected control path fail rather than dispatching an ungoverned
replacement call. No inbound listener is created.

```python
from atellagent_client.connected import mount_agent_handler
from atellagent_client.sdk import ConnectedSDKRuntime

async def connect_agent(workload_context, request_id, customer_handler):
    config, _ = await workload_context.runtime(request_id)
    runtime = ConnectedSDKRuntime(config)
    mount_agent_handler(runtime, customer_handler, consequential=True, target_idempotent=True)
    return runtime
```

Use `mount_model_handler`, `mount_filter_handler`, `mount_mcp_handler`,
`mount_workflow_handler`, or `mount_channel_registry` only for the matching
connected integration type. See [custom integrations](../docs/BUILDING_CUSTOM_INTEGRATIONS.md)
for minimal runnable patterns.

An agent handler can instantiate `AgentParticipantActions` to make
`invoke_model_async(messages, ...)` and
`call_mcp_tool_result_async(target, tool, arguments, tool_call_id=...)` calls
while handling its delivery. The gateway binds these calls to that delivery's
workflow and current identity. The action object is unavailable outside the
handler; do not save it or use it in detached background tasks.
Model effect keys are derived from the input unless an explicit stable
`effect_key` is supplied. Repeating an accepted effect reuses its submission;
changing its input under the same key fails. Tool calls use their model-generated
IDs. An uncertain submission fails rather than making an ungoverned replacement.

## Exclusions and diagnostics

This is not an MCP compatibility proxy, an external-host hook, or a provider
route facade. It does not grant identity to a caller-supplied principal. Use
the participant's sanitized logs and correlation identifiers for diagnostics;
do not include private keys, certificates, enrollment tokens, or payloads in a
support request.


### Hosted model context accounting

A connected model used by a hosted Agent must implement the optional
`ModelContextRuntimeHandler.measure_model_context(request)` contract alongside
`invoke_model`. Mount it with the existing `mount_model_handler`; a request with
`context_operation="measure"` calls measurement only. An older or generation-only
handler rejects measurement without invoking generation. Ordinary direct model
invocations keep their existing behavior.

Return the closed `atellagent.connected.model-context.v1` document, validated by
`atellagent_client.protocol.model_context.validate_model_context_result`: `model`,
`request_digest`, integer `input_tokens`, boolean `estimated`, stable
`counter_version`, and exact supported input/context and output `limits`. Compute
`request_digest` with `model_context_digest(request)`. Count the complete
`model_context_input(request)` using the selected model's actual tokenizer and
framing; capacity declarations or a generic character estimate are not token
accounting. Update `counter_version` when tokenizer/framing semantics change.
Never call generation or log input inside measurement.

Atellagent validates model measurements before authorizing generation. If a
measurement fails, generation is not permitted.

## Protect your MCP server

[Enterprise MCP](../docs/ENTERPRISE_MCP.md) adds a direct intake to the same connected MCP runtime and mounted handler. Your server authenticates callers; Atellagent authorizes each action before
execution. External MCP traffic is blocked by default. An administrator configures
**Allow external MCP traffic** during setup or in **Policies → Rules → Connection
controls**. The **External MCP traffic** shortcut in **Runtimes & Integrations**
edits the same setting.
