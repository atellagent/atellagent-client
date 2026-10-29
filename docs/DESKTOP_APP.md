# Desktop app

The Atellagent app runs a current-user service on macOS or Windows for device
discovery, approved coding-agent controllers and local MCP bridges. It does not
install agents or MCP servers, and discovery alone does not authorize them.

## Install and enroll your device

Download the installer for your operating system from
[Atellagent integrations](https://atellagent.com/integrations) or Organization →
Runtimes & Integrations in the product. Check the listed architecture, system
requirements and signing status. Follow your company's software approval process;
do not disable OS security protections to install a release.

On Overview, name the device and optionally select Codex, Claude Code and local
MCP servers. You can request these connections together with the device, or add
them later in Connections. Sign in through the displayed browser link/code and
choose your organization.
No platform configuration file or enrollment token is needed. An organization
administrator reviews the requests together in Discovery and can approve or deny
each separately. Existing organization permissions still govern who may request
MCP integrations. Device approval alone does not authorize any connection.

While the app is open, approved connections automatically finish credential
setup and remain off until you enable them. Device
reporting can run without any agent or MCP connection. If you stop waiting or
close the app during setup, continue the original request rather than submitting
another one. Continue setup also retries any unconfirmed connection submissions
using their original requests and keys. The submitted-request count does not mean
the integrations are approved or connected.

## Connect a coding agent

In Connections, request a supported agent connection or pick up an administrator's
assignment. Requesting an agent authorizes agent.control on this device until
revoked; administrator approval remains required. An administrator-prepared
assignment needs your separate identity/scope review and consent. Device approval
does not grant agent access.

Turn on **Enabled** for a ready connection to configure its hooks and start its
controller. The toggle authorizes these local changes; unrelated settings are
preserved, and conflicting configuration is not silently overwritten.
On macOS, this includes the hooks and
the preferred local MCP bridge for both Codex and Claude Code. The bridge uses
the exact selected controller endpoint; host settings contain no credentials.
Codex's entry is in `~/.codex/config.toml`; Claude Code's user-scoped entry is in
`~/.claude.json`, separately from `~/.claude/settings.json` hooks. Inspect both
settings when resolving a conflict. A different existing `atellagent` MCP entry is a conflict;
it is never silently replaced. Removal deletes only exact generated entries.
Windows currently configures hooks only; its bridge setup/acceptance remains a
separate gate.

Follow the host's restart or new-conversation instructions
and verify a harmless governed action. Local controller availability does not
prove hooks are active or that the action is allowed by policy.

One service can run the selected Codex and Claude Code controllers together.
Only one connection is enabled per supported host. Turn it off before enabling
a replacement. Turning off removes its managed hooks and local bridge entry,
then stops its controller without revoking its approved identity.
Other connections and device reporting can remain running.

The native hook permission to invoke MCP does not authorize a particular
integration or operation. The local bridge exposes `atellagent_list_servers`,
`atellagent_list_tools`, `atellagent_describe_tool` and `atellagent_call_tool`.
The controller uses its authenticated identity and active owner delegation to
resolve registered targets, and actual dispatch separately authorizes the
integration, operation and arguments. Names reported by a direct host connector
are not proof that it uses this route. Discovery lists configuration entries; it
does not approve them or attest an execution endpoint. Hook verification also
does not prove MCP bridge readiness; verify a harmless registered-tool call.

## Connect an existing MCP server

Use **Connect an MCP server** in Connections. Choose a detected server, add a
local streamable HTTP URL, or choose an executable for a stdio server. Discovery
is optional; manually added servers do not need a Codex or Claude configuration
entry. Give the connection a name and request approval. Use Advanced only for
credential variable references or to distinguish duplicate configuration entries.
Administrator-assigned connections use the same target selection and review.

For a detected server, the app reads the target from your current-user settings.
Manual HTTP targets must use a loopback address. For stdio, enter each executable
argument on its own line, without shell quoting. Discovery does not
contact it, install it or upload its command, endpoint or credentials. When
needed, supply credential environment-variable names, not secret values; the
variables must be available to the local service that runs the bridge.

Select discovered MCP servers using the setup checkboxes, or add a local target
manually. They are submitted together with your selected agents and reviewed
individually by your administrator. Approved targets finish credential setup
automatically while the app is open. Turn on **Enabled** when ready to run one.
Enabling a stdio target launches its executable; enabling an HTTP target contacts
its local endpoint. Supported targets must meet the
[connected MCP protocol and delivery contract](../connected/README.md#public-entry-points-and-failure-behavior).
Not every discovered MCP entry is a compatible bridge target.

Turning **Enabled** off removes the background selection, not the server-side
integration's permissions. **Manage integration** opens the exact account in
Runtimes & Integrations. An administrator revokes cloud access separately.

## Reporting and authorization

Discovery reports bounded current-user facts, not prompts, raw settings,
endpoints or credentials. Name the device so administrators can distinguish it
from your other installations. Reporting can be turned off without stopping
agent controls; Discovery shows the reporting state, and the organization can
receive an alert when reporting stops. A stale report is not a current inventory.

Background operation uses approved machine credentials. Native agent actions
also require the exact durable owner delegation. Management sign-in is for setup
and consent, not for every service start or action. Browser logout alone does
not revoke approved connections; access removal, revocation, configured expiry
and current policy still apply. Expired or revoked machine credentials require
[approved recovery](LOCAL_SERVICE.md#remove-local-enrollment-or-recover-a-device),
not repeated browser login.

## Update the app

In Settings → Updates, use **Check for updates**. Automatic checks can be
enabled or disabled. When a release is available, **Download update…** opens
the installer download in your browser; it does not install it or restart the
service.

Stop the local service from Connections and quit Atellagent before running the
installer. Complete installation, reopen the app and start the service. Approved
local configuration and credentials remain; an ordinary update does not require
new device or connection requests. Check the new version and verify connection
status afterward. Company IT can manage installation instead.

For terminal operation, hook configuration and exact request continuation
commands, use the [local device service guide](LOCAL_SERVICE.md).
