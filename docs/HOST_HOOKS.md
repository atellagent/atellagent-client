# External coding-host hooks

Atellagent Client is the local enforcement path for Agent Authority at the
documented host-hook checkpoints. The `atellagent-hook-adapter` command
connects a supported host command hook to an enrolled, local Atellagent
hook-control service. The hook command has no credential or service-account
configuration: it receives host JSON on standard input and can only reach the
owner-private local endpoint you configure: a Unix socket on macOS/Linux or a
current-user ACL-protected named pipe on Windows.
Codex and Claude Code are the supported release hosts. Gemini CLI is a preview
adapter whose release acceptance is still pending.

## Installation order

Start with the [local device service guide](LOCAL_SERVICE.md). Enroll the device
through browser sign-in and administrator approval, then request the native
connection. Administrator approval and the device owner's consent are separate;
no downloaded identity YAML or copied enrollment token is required. Private
keys are generated on the machine running the controller.

```bash
atellagent-cli setup native request --host codex --name "Coding agent"
atellagent-cli setup native consent <request-id>
atellagent-cli setup native resume <request-id>
atellagent-cli setup native select <request-id>
```

Use `--host claude-code` for a Claude Code connection. Retain the request UUID:
continuation uses the saved, exact request. Selecting a connection does not
install hooks or approve discovered MCP entries. Preview and apply the exact
hook settings as described in the service guide, then start the background
service. A foreground controller can instead run the activated request:

```bash
atellagent-cli setup native run <request-id> \
  --hook-control-socket /run/user/<uid>/atellagent/control.sock
```

Starting the controller registers its runtime instance; no separate runtime
registration command is needed. Enable hooks only after its private endpoint
is available, and verify a real governed request.

Normal execution uses separate machine credentials, device proof and the exact,
revocable owner delegation. It does not repeatedly ask for browser sign-in
because a management session expired. Current permissions, policy and revocation
still apply. Human sign-in is required for attended setup and owner consent;
administrator approval cannot substitute for that consent. No user token or
private key belongs in host settings or hook commands.

Use `setup native unselect <request-id>` to remove a local controller selection.
This retains credentials and does not revoke server access or remove installed
hooks. Revoke authorization through the organization's management UI when access
must end. Local status alone does not prove current server authorization.

This local authorization is used by the hook controller and local bridge. It
is separate from signing in to the public Atellagent MCP endpoint. The public
endpoint does not require a local bridge. Configured host hooks can still
inspect calls to that endpoint when the host emits the supported tool events.

For native Codex and Claude Code, prefer the desktop app's local bridge. On
macOS, review **Hooks & MCP bridge** for the selected controller and apply the
exact changes. This adds Codex's MCP entry in `~/.codex/config.toml` or Claude
Code's user-scoped entry in `~/.claude.json`, alongside its separate hooks.
Unrelated settings are preserved; conflicting `atellagent` entries require
explicit resolution. Windows automatic bridge setup remains pending.

For a manual Codex setup, use the same installed CLI and exact controller socket:

```toml
[mcp_servers.atellagent]
command = "/absolute/path/to/atellagent-cli"
args = ["mcp-bridge", "--socket", "/absolute/path/to/controller.sock"]
startup_timeout_sec = 30
```

For Claude Code, the equivalent user-scoped `~/.claude.json` entry is:

```json
{
  "mcpServers": {
    "atellagent": {
      "type": "stdio",
      "command": "/absolute/path/to/atellagent-cli",
      "args": ["mcp-bridge", "--socket", "/absolute/path/to/controller.sock"]
    }
  }
}
```

Merge this entry with existing settings; do not replace the entire file. The
host owns the stdio process. The legacy `atellagent-mcp-bridge --config` command
and endpoint-only YAML remain supported. Neither entry contains credentials;
the controller retains its authenticated identity and revocable owner grant.
The bridge exposes the four dispatcher tools; actual registered target and
operation authorization occurs separately at dispatch. Direct connector names
never establish that authorization. Native MCP invocation permission requires
explicit policy selection and does not grant integration access.

For a separate customer-MCP boundary, use the
[managed workload flow](../connected/README.md), not this credential-free bridge.

Use an absolute socket path on macOS/Linux or a local `\\.\pipe\<name>` endpoint
on Windows. The adapter uses a 19-second local deadline. The Codex and Claude
Code templates give the host a 20-second command timeout; the Gemini CLI
template uses the equivalent 20,000 milliseconds. This leaves the adapter time
to return an explicit denial before the host timeout. Keep the entire timeout
chain aligned when changing a host setting; changing only the host timeout
does not lengthen the overall review deadline.

## Coverage

Claude Code and Codex `UserPromptSubmit` are governed as a `turn_entry` model
decision: the request contains exactly the one submitted user prompt. It covers
that prompt before the host processes it, not subsequent model requests.
Gemini CLI `BeforeModel` is governed as a `full_model_request` decision because
that documented hook supplies the outbound model request. `PreToolUse` is a
synchronous preflight. Claude Code records correlated success and failure
outcomes through `PostToolUse` and `PostToolUseFailure`. Codex records a
correlated result when it emits `PostToolUse`, but it does not provide a
separate failure event or guarantee a callback for every completed tool call.
An absent callback remains an unreported outcome, not a confirmed success or
a result that Atellagent inspected before model exposure.

For callback-capable hosts, the local control service retains a small private
delivery record so a restart can retry outcome delivery. It does not retain raw
tool arguments, raw result content, or credentials. A host without a documented
correlated post-tool callback is not represented as a local delivery failure.

Hook-visible MCP calls receive the same supported hook checks as other tools.
An MCP destination may apply its own enforcement independently. A tool name
alone does not establish its destination or coverage.
Codex hosted or specialized tool paths outside its documented command-hook
surface are not covered by this adapter. This guide does not imply coverage of
Claude Cowork.
Codex `update_goal` calls whose input is exactly `status=blocked`,
`status=paused`, or `status=complete` use normal preflight and postflight while
the controller can evaluate policy, so policy can govern them. If quota admission,
authorization, native profile/adapter coverage, the local controller, or directive
verification fails or an evaluation returns a technical hold, the adapter lets
those exact transitions finish so a task
cannot be trapped in an unusable goal state. Unsupported delivery obligations
also allow terminal goal recovery. Authored policy refusals and verified directive
denials still block the transition, even if their reason resembles an admission
error. Ordinary tools continue to fail closed on every such failure. The host remains responsible for validating
goal state. A user clicking Pause uses the host's own control, not this tool
exception.

A local authorization does not make a host hook tamper-proof or inspect
arbitrary outbound traffic. A tool can be denied only when the installed host
emits the documented event and honors the adapter response. Manage the host
configuration and any other provider access separately.

The complete public coverage declaration is available from
`atellagent_client.integrations.agents.host_hook_capabilities()` and in
`examples/config/host-hook-capabilities.json`.

Read the host-specific coverage matrices before deployment:
[Claude Code](hosts/claude-code.md), [Codex](hosts/codex.md),
[Gemini CLI](hosts/gemini-cli.md), and
[Claude Cowork (deferred)](hosts/claude-cowork.md).

## Claude Code

Use command hooks, not HTTP hooks: command-hook exit code 2 blocks a prompt or
tool event, whereas an HTTP hook connection failure is non-blocking. On
macOS/Linux, add `examples/config/claude-code-hooks.user.json` to the user's
Claude Code settings, replacing `<uid>` with the runtime user's numeric UID.
On Windows, start the controller with a local named pipe and use
`examples/config/claude-code-hooks.windows.json` instead.

An administrator can use the same hook block in Claude Code managed settings;
the committed template is `examples/config/claude-code-hooks.managed.json`.
Pin the executable path and its package release through the administrator's
managed software mechanism, and restrict write access to both settings and the
socket parent. A user or administrator that disables a hook, or a host that
never starts it, remains outside the adapter's control boundary.

## Codex

On macOS/Linux, add the TOML in `examples/config/codex-hooks.user.toml` to the
user-level `~/.codex/config.toml` (or a trusted project configuration where
appropriate).
It uses synchronous command handlers for `UserPromptSubmit`, `PreToolUse`, and
`PostToolUse`. Codex command hooks run in a sandbox, so the template adds its
exact-path Unix-socket allowlist for the credential-free adapter to reach its
owner-private control service. The hook command invokes the adapter directly;
nesting `codex sandbox` inside a hook is unsupported. Do not substitute a
parent-directory allowance or a TCP listener. Fully restart Codex after
changing hook, MCP-server, or socket-permission configuration.

The examples give the three handlers descriptive `statusMessage` values:
"Atellagent: checking prompt", "Atellagent: checking tool action", and
"Atellagent: recording tool result". These messages describe work while a
hook runs; Codex may still number handlers in its hook browser because its
documented configuration has no per-handler display-name field. Review and
activate the hooks from a Codex chat before relying on them.

The same TOML configures `atellagent-mcp-bridge`, the local MCP entry. The
matching hook-control service requires an activated connection and its approved
owner delegation before accepting hook or MCP input. Management sign-in alone
does not authorize runtime actions. Do not place approved runtime settings or
credentials in `~/.codex/config.toml`.

For strict host-hook coverage, keep the bridge server name exactly
`atellagent`. Its tool names use the documented
`mcp__atellagent__mcp_<operation>_<24-hex-id>` form. That name does not grant
access to a tool; the local bridge still checks authorization. Do not add a
wildcard MCP allow rule or a second direct server to bypass this boundary.

For managed deployment, place the corresponding block from
`examples/config/codex-hooks.managed.toml` in the administrator-managed Codex
configuration, set an absolute `hooks.managed_dir` containing the pinned
adapter executable, and set `allow_managed_hooks_only = true` in
`requirements.toml` when users must not substitute user, project, session, or
plugin hook configuration. Pin the installed client release and ensure only the
administrator can modify the managed configuration and executable directory.

### macOS

Use `examples/config/codex-hooks.macos.toml` on macOS. The template uses a
dedicated PostToolUse launcher which relays the documented host event unchanged
to the ordinary adapter. For a foreground deployment, run the activated native
request with the same owner-private socket path before opening Codex:

```bash
atellagent-cli setup native run <request-id> \
  --hook-control-socket /private/tmp/atellagent-codex-hook-control/control.sock
```

Approved settings are delivered automatically to the saved request. For a
background service, use the reviewed hook preview/apply flow in the
[service guide](LOCAL_SERVICE.md), rather than substituting this foreground
endpoint. No certificate material belongs in `~/.codex/config.toml`.

Codex's managed configuration layers, inline `[hooks]` syntax, and
`allow_managed_hooks_only` setting are documented by OpenAI. Host hooks are an
additional enforcement point, not a replacement for host permissions or a
claim that a disabled host hook is enforced.

### Windows

For a foreground deployment, run the activated native request with a local named pipe. The control
runtime creates that pipe with a DACL granting the current Windows user only;
it also rejects remote pipe clients.

```powershell
atellagent-cli setup native run <request-id> `
  --hook-control-socket '\\.\pipe\atellagent-hook-control'
```

Use `examples/config/codex-hooks.windows.toml` for Codex and
`examples/config/local-mcp-bridge.windows.yaml` for its local MCP bridge. Use
`examples/config/claude-code-hooks.windows.json` for Claude Code. These are
native Windows endpoint templates, so they do not use a Unix-socket sandbox
allowlist. Validate the exact host release and its command-hook behavior before
representing a Windows host integration as supported.

Codex launches command hooks from the chat's working directory. For a native
Windows Codex chat, choose a directory that exists and is accessible to the
Windows runtime. A stale `\\wsl$\...` project path can prevent the hook process
from starting with Windows error 267 (invalid directory), before the adapter
receives an event. Open a chat in a valid Windows directory, or run both Codex
and the control runtime inside WSL2 with the Linux socket template below.

### WSL2

WSL2 is a Linux deployment, not a Windows named-pipe deployment. When the host
agent and the control runtime both run in the same WSL2 distribution, use the
Linux Unix-socket templates and a separately approved Linux installation with
owner-private state. A native Windows host agent uses the Windows pipe templates
instead. Do not bridge the local routes over TCP or share their device/agent keys.

## Gemini CLI

This is a preview setup for development testing, not a supported release host.
Add `examples/config/gemini-cli-hooks.user.json` to trusted Gemini CLI
settings, replacing `<uid>` with the runtime user's numeric UID. The template
governs `BeforeModel` and `BeforeTool` synchronously. It does not configure
`AfterModel`, rewrite requests or responses, or claim tool-outcome recording
where Gemini does not provide a stable tool-call identifier for correlation.

## Diagnostics and failures

The adapter emits only documented host allow/deny responses. Malformed input,
adapter errors, an absent local control service, or a service failure return
exit code 2 and do not disclose internal service detail. Policy denials and
pre-tool review/control timeouts use the host's structured denial format. A
timeout message may invite one unchanged retry; the tool remains blocked unless
a subsequent check permits it. This is guidance to the host model, not automatic
retry scheduling. Policy denials do not invite retries. Other event timeouts
return exit code 2. Hook stdout is
reserved for the host response; use the host's normal command-hook diagnostics
to inspect failures.

The control service health response exposes non-secret readiness and outcome
delivery status. Restarting the controller does not clear pending local outcome
delivery. If it reports a problem, correct the controller or host configuration
rather than deleting local state.
