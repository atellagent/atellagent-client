# External coding-host hooks

The `atellagent-hook-adapter` command connects a supported host command hook to
an enrolled, local Atellagent hook-control service. The hook command has no
credential or service-account configuration: it receives host JSON on standard
input and can only reach the owner-only Unix socket you configure.

Start the local control service with an enrolled connected-agent configuration:

```bash
atellagent-cli ./hook-control.yaml \
  --hook-control-socket /run/user/<uid>/atellagent/control.sock
```

Use an absolute socket path in the host configuration. The adapter uses a
seven-second local deadline; all templates below give the host an eight-second
command timeout so an unavailable control service results in the adapter's
exit-2 denial before the host timeout expires.

## Coverage

Claude Code and Codex `UserPromptSubmit` are governed as a `turn_entry` model
decision: the request contains exactly the one submitted user prompt. It covers
that prompt before the host processes it, not subsequent model requests.
Gemini CLI `BeforeModel` is governed as a `full_model_request` decision because
that documented hook supplies the
outbound model request. `PreToolUse` is a synchronous preflight.
`PostToolUse` records the correlated outcome; Claude Code also supplies
`PostToolUseFailure`.

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
`PostToolUse`.

For managed deployment, place the corresponding block from
`examples/config/codex-hooks.managed.toml` in the administrator-managed Codex
configuration, set an absolute `hooks.managed_dir` containing the pinned
adapter executable, and set `allow_managed_hooks_only = true` in
`requirements.toml` when users must not substitute user, project, session, or
plugin hook configuration. Pin the installed client release and ensure only the
administrator can modify the managed configuration and executable directory.

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
