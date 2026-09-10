# External coding-host hooks

Atellagent Client is the local enforcement path for Agent Authority at the
documented host-hook checkpoints. The `atellagent-hook-adapter` command
connects a supported host command hook to an enrolled, local Atellagent
hook-control service. The hook command has no credential or service-account
configuration: it receives host JSON on standard input and can only reach the
owner-only Unix socket you configure.

Start the local control service with an enrolled connected-agent configuration:

```bash
atellagent-cli ./hook-control.yaml \
  --hook-control-socket /run/user/<uid>/atellagent/control.sock
```

To expose governed external MCP tools from the same Codex runtime, start
`atellagent-mcp-bridge --config ./bridge.yaml`. The bridge configuration names
the same absolute socket; the enrolled control service remains the sole local
credential holder and fetches its assigned public tool catalog. Start from
[`examples/config/local-mcp-bridge.yaml`](../examples/config/local-mcp-bridge.yaml).
Do not use `tool-proxy.yaml`: that example is for a separately enrolled
customer-MCP boundary.

Use an absolute socket path in the host configuration. The adapter uses a
seven-second local deadline; all templates below give the host an eight-second
command timeout so an unavailable control service results in the adapter's
exit-2 denial before the host timeout expires.

## Coverage

Claude Code and Codex `UserPromptSubmit` are governed as a `turn_entry` model
decision: the request contains exactly the one submitted user prompt. It covers
that prompt before the host processes it, not subsequent model requests.
Gemini CLI `BeforeModel` is governed as a `full_model_request` decision because
that documented hook supplies the outbound model request. `PreToolUse` is a
synchronous preflight. Claude Code records correlated success and failure
outcomes through `PostToolUse` and `PostToolUseFailure`. Codex records a
correlated tool result through `PostToolUse`; Codex does not provide a separate
failure event.

For callback-capable hosts, the local control service keeps a private, bounded
delivery record after receiving the post-tool event so a local restart cannot
lose the terminal observation. That record contains an opaque action binding
and result size/digest only; it does not retain tool arguments, raw result
content, credentials, or the signed directive. A host without a documented
correlated post-tool callback is not represented as a local delivery failure.

Calls to the `mcp__atellagent__*` facade deliberately skip host preflight and
postflight because that facade is governed at its MCP effect boundary. Codex
hosted or specialized tool paths that are outside its documented command-hook
surface are not covered by this adapter. This guide does not imply coverage of
Claude Cowork.

The complete public coverage declaration is available from
`atellagent_client.integrations.agents.host_hook_capabilities()` and in
`examples/config/host-hook-capabilities.json`.

Read the host-specific coverage matrices before deployment:
[Claude Code](hosts/claude-code.md), [Codex](hosts/codex.md),
[Gemini CLI](hosts/gemini-cli.md), and
[Claude Cowork (deferred)](hosts/claude-cowork.md).

## Claude Code

Use command hooks, not HTTP hooks: command-hook exit code 2 blocks a prompt or
tool event, whereas an HTTP hook connection failure is non-blocking. Add the
contents of `examples/config/claude-code-hooks.user.json` to the user's Claude
Code settings, replacing `<uid>` with the runtime user's numeric UID.

An administrator can use the same hook block in Claude Code managed settings;
the committed template is `examples/config/claude-code-hooks.managed.json`.
Pin the executable path and its package release through the administrator's
managed software mechanism, and restrict write access to both settings and the
socket parent. A user or administrator that disables a hook, or a host that
never starts it, remains outside the adapter's control boundary.

## Codex

Add the TOML in `examples/config/codex-hooks.user.toml` to the user-level
`~/.codex/config.toml` (or a trusted project configuration where appropriate).
It uses synchronous command handlers for `UserPromptSubmit`, `PreToolUse`, and
`PostToolUse`. Codex command hooks run in a sandbox, so the template adds its
exact-path Unix-socket allowlist for the credential-free adapter to reach its
owner-private control service. The hook command invokes the adapter directly;
nesting `codex sandbox` inside a hook is unsupported. Do not substitute a
parent-directory allowance or a TCP listener. Fully restart Codex after
changing hook, MCP-server, or socket-permission configuration.

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
to the ordinary adapter. Start the downloaded dashboard runtime configuration
with the same owner-private socket path before opening Codex:

```bash
atellagent-cli ./downloaded-runtime.yaml \
  --hook-control-socket /private/tmp/atellagent-codex-hook-control/control.sock
```

The dashboard-generated runtime configuration is authoritative; only the local
socket path and matching Codex template are host-specific. Do not place the
runtime configuration, enrollment token, or certificate material in
`~/.codex/config.toml`.

Codex's managed configuration layers, inline `[hooks]` syntax, and
`allow_managed_hooks_only` setting are documented by OpenAI. Host hooks are an
additional enforcement point, not a replacement for host permissions or a
claim that a disabled host hook is enforced.

## Gemini CLI

Add `examples/config/gemini-cli-hooks.user.json` to trusted Gemini CLI
settings, replacing `<uid>` with the runtime user's numeric UID. The template
governs `BeforeModel` and `BeforeTool` synchronously. It does not configure
`AfterModel`, rewrite requests or responses, or claim tool-outcome recording
where Gemini does not provide a stable tool-call identifier for correlation.

## Diagnostics and failures

The adapter emits only documented host allow/deny responses. Malformed input,
adapter errors, an absent local control service, timeout, or a service failure
return exit code 2 and do not disclose internal service detail. A normal policy
denial is rendered in the host's structured denial format. Hook stdout is
reserved for the host response; use the host's normal command-hook diagnostics
to inspect failures.
