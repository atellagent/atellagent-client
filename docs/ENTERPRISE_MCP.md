# Protect an MCP server with Enterprise MCP

Enterprise MCP extends the existing connected MCP runtime. Keep your enrollment,
credentials, session, tool publication, receiver and mounted handler. Calls
through Atellagent retain their connected capability and lease checks. Direct
clients obtain central authorization after your server receives a tool call.
Both execute the handler registered with `mount_mcp_handler`.

## Setup

1. In the existing setup modal, choose **Protect an MCP server**, or prepare an
   MCP server with SDK/bridge packaging and organization settings. Complete the
   normal operator assignment, approval, enrollment and activation.
2. Mount the actual tool dispatcher once. Start the runtime normally so it
   publishes the same manifest used by connected calls.
3. Authenticate inbound requests with your server's existing OAuth/JWT/session
   checks, including issuer, audience and resource permissions. At the final
   tool dispatch boundary, call `runtime.handle_mcp_request` below.
4. Review effective policy and test harmless allowed and denied calls. An
   organization administrator controls **Allow external MCP traffic** during
   setup or in **Policies → Rules → Connection controls**. Choose **MCP server**
   and select your connection. The **External MCP traffic** shortcut in
   **Runtimes & Integrations** edits the same setting. Its default is off.

```python
mount_mcp_handler(runtime, execute_tool, consequential=True, target_idempotent=True)

# Inside your existing, authenticated tools/call dispatcher:
caller = caller_from_auth_context(request.auth_context)
response = await runtime.handle_mcp_request(
    mcp_request,
    idempotency_key=logical_action_key,
    caller=caller,
)
```

`execute_tool(request, idempotency_key)` is the same handler for both intakes.
Every protected tool dispatch must pass through it. A sidecar pointing at a
server does not protect another listener that bypasses this dispatch boundary.
Keep your normal target permissions and native authentication in place.

## Native caller adapter

Use the server's **already validated** context. This example assumes your auth
middleware has verified the token or session; it does not parse a raw JWT.

```python
def caller_from_auth_context(context):
    return {
        "issuer": context.verified_issuer,
        "subject": context.stable_subject,
        "account_id": context.provider_account_id,
        # Optional, bounded scalar attributes used by an applicable policy:
        "attributes": {"region": context.verified_region},
    }
```

Issuer and subject are required when supplying a caller. Include `account_id`
when the provider scopes subjects to an account or tenant. Identifiers should
be opaque and stable. They identify a person or workload without requiring a
classification, an Atellagent user account, email linking or a new identity
mapping system. The same summary is bound to the decision, action and outcome.
If no distinct identifier is available, pass `caller=None`; records explicitly
show boundary-only correlation. This still requires your native authentication.

Never build trusted identity from tool arguments, an arbitrary user-ID header,
or unverified JWT claims. Never forward bearer tokens, refresh tokens or whole
JWTs to Atellagent. Your server already has the verified context; a provider
does not need to add another user login or send its credentials to Atellagent.
A vendor operating its server installs this hook; connecting a provider account
as a customer does not install protection on the vendor's behalf.

## Authorization and outcomes

The runtime requests authorization for the published tool and its arguments
through `POST /v1/mcp/boundary/preflight`, using its enrolled session. The client
verifies that the signed authorization matches the tool, arguments, connection
and request before invoking the mounted handler. Pending approval, invalid
or unavailable authorization, an unpublished tool, or changed tool publication
prevents execution.

After execution, the runtime reports the outcome through
`POST /v1/mcp/boundary/postflight`. The result is returned only when the response
check allows it. Withholding a response does not undo an action that has already
executed.

Turning direct clients off takes effect centrally without restarting the PEP.
It blocks new admissions and unreleased direct actions, while existing centrally
authorized connected calls continue. It cannot undo a completed effect.
This setting is separate from Enforce/Observe, SDK/bridge packaging, local
manifests and Native blocks. Direct intake requires organization settings;
Observe records organization-policy effects while mandatory authorization and
platform protections still apply.

Keep arguments consistent: supply the effective arguments after native input
validation/default resolution, and do not change them between authorization
and the actual effect. Use the same published tool behavior for connected calls.
MCP metadata and headers cannot confer Atellagent authority. Reject supplied
connected capabilities or control directives at an independent public intake;
they belong on the existing verified connected transport.

## Retries, lifecycle and example

Choose a stable logical `idempotency_key`; the gateway namespaces it to the
resource and native caller. Consequential handlers must enforce the **supplied
handler key** durably at their target. Do not generate a new key when a request
or result is uncertain. Reusing a logical action with different arguments is
rejected. A duplicate active/completed action may return a conflict rather than
execute again; do not interpret that conflict as permission to bypass the gate.
The MCP request ID is transport correlation, not execution authority.

Direct calls share the participant's concurrency, shutdown and credential
rotation lifecycle. The runtime creates no new listener, supervisor, credential
store or tool catalog. The SDK and bridge inherit the same direct method; your
existing server owns its listener and dispatch integration.

Run the small [reference server](../examples/mcp/resource_server.py) using an
approved SDK MCP workload. It uses a local bearer-key check as its own native
authentication example and derives a fixed opaque subject from server-side
configuration. Replace that adapter with your real verified context. The echo
tool is pure; consequential production tools must honor the target key durably.
