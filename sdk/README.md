# Python SDK

## Purpose and placement

Use the SDK inside Python code you own. It creates a connected runtime or a
direct client call using an approved, certificate-authenticated workload. It is a
`connected` placement and `sdk` integration surface.

## Authentication and public entry points

Use the asynchronous `create_service_account_client` factory with the workload's
saved `WorkloadSetupFlow`. It resumes approval and credential activation and
receives trusted runtime endpoints. It does not start browser sign-in or accept
caller-supplied account IDs, certificate paths or enrollment tokens. Complete
attended setup and administrator approval before starting runtime execution.
Provider keys and integration-specific bridge targets remain with customer code.

Attended setup is available through `DeviceLoginClient`, `SetupManagementClient`
and `WorkloadContext` in the public SDK. Present the link/code returned by
`DeviceLoginClient.begin()` and await `wait()`; the browser may be on another
device. Review the memberships returned by `SetupManagementClient.identity()`
and explicitly select the organization. `WorkloadContext.prepare_request()`
saves intent and local inputs before `submit_request()` sends the request.
Keep the state directory and request ID to continue with `flow(request_id)`.
`setup_progress()` reports approval and activation separately from registration;
ending the wait does not cancel the request. None of these setup calls starts
a runtime. Device-associated native setup uses `DeviceSetup` and
`NativeSetupContext`, not a caller-supplied device ID.

For a complete request/approval/activation/runtime example, run
`python -m atellagent_client.examples.onboarding --help`. It supports continuing
from the retained request ID without signing in again. Use
`WorkloadContext.open_runtime()` to start an approved SDK or bridge participant
with paired cleanup. Python callers can supply their handler directly;
CLI/service deployments can retain a `module:attribute` reference instead.

An MCP service account identifies the server/bridge integration, not the user
who configured it. Setup sign-in does not delegate user permissions to later
SDK calls. Those calls use the verified machine/principal authority unless an
explicitly supported delegation establishes an acting user.

For device-associated MCP integrations, open outgoing calls with
`DeviceMCPContext.open_client(request_id)` or pass a flow obtained from that
verified context to `create_service_account_client()`. The shared transport
adds fresh device evidence to each request without granting the device owner's
user permissions. A standalone `WorkloadContext` remains device-less; copying
a device ID into settings does not establish device admission.

```python
from atellagent_client.sdk import create_service_account_client

async def connect(saved_workload_flow):
    return await create_service_account_client(flow=saved_workload_flow)
```

Close the returned client using its asynchronous context manager or
`await client.close_async()`. Pending, denied, revoked or unavailable setup is an
error, not permission to provision another identity. Routine service-account
requests use machine authentication; they do not depend on a continuing browser
session. `ConnectedSDKRuntime` can use the resulting typed
`client.service_account_config` with the customer's operation handlers.

Use `boundary_identity_only` unless a custom integration can present configured,
verifiable external identity evidence. The standard external-host hooks do not
accept caller-supplied identity.

## Coverage and failures

The SDK can participate in `decision` or `route` model governance where the
matching integration documents it. It can also submit tool calls through the
documented boundary. Workflow context is optional: it can add context to a
call, but a workflow is not required for policy to apply.

### Model conversation context

`invoke_model_async(messages, provider=..., model=...)` uses caller-managed
context by default. Supply the messages your application needs; no platform
memory thread is required or created. This can still be a stateful application
using its own conversation store.

Atellagent Chat creates and owns its conversations. Connected agents can receive
that conversation and verified user/execution context through a Web Chat workflow.
Platform-managed model requests accept one new user message, not a replacement
history. An authorized conversation handle identifies the server-owned thread;
it is not a credential and must not be invented or borrowed from another user.

An organization can require platform-managed context through Model Access policy.
Caller-managed requests then fail policy admission; do not retry without the
required context or switch to a direct provider. Setup ownership alone does not
grant access to the owner's conversations.

Authentication, validation, and control failures raise documented SDK errors;
do not catch them and retry through an ungoverned provider or tool path. This
guide does not document private service behavior or support underscored modules.
