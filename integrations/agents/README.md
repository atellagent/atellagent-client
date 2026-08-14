# Agent integrations

## When to use this package

Use agent contracts for a customer-operated agent boundary, an external-host
hook control service, or normalized customer tool execution metadata. This is
not an MCP server merely because it governs tool actions.

## Host hook control

`HookControlRuntime` runs an enrolled `agent.control` boundary on an owner-only
Unix socket. `atellagent-hook-adapter` is credential-free and translates Claude
Code or Codex command-hook JSON to that socket. Standard host hooks use
`boundary_identity_only` and provide `turn_entry` model decisions plus tool
preflight/postflight, not `full_model_request` observation.

```bash
atellagent-cli ./hook-control.yaml \
  --hook-control-socket /run/user/<uid>/atellagent/control.sock
```

See [external host documentation](../../docs/HOST_HOOKS.md) for installation,
coverage, and host bypass boundaries.

The separate loopback [Anthropic Messages facade](../../docs/hosts/claude-code-route-mode.md)
is a non-streaming route facade, not a Claude Code deployment: Claude Code
needs event streaming. It retains the enrolled boundary
inside the facade and does not replace the host-tool effect boundary.

The [OpenAI Responses facade](../../docs/hosts/codex-route-mode.md) provides
the same non-streaming route facade. It is not a Codex custom-provider
deployment.

## Public contracts and failures

Use `BoundaryToolCall`, `BoundaryExecutionMetadata`, `HookControlRuntime`,
`HookControlClient`, and `host_hook_capabilities` from this package. The local
socket rejects malformed authority fields; adapter, socket, directive, and
control failures deny rather than manufacture a local allow. Customer code
must not depend on underscored modules or inject an unverified principal ID.

This package does not implement a provider route facade, a workflow engine, or
the actual MCP tool effect boundary.
