# Codex coverage

Atellagent supports Codex through synchronous local `command` hooks and an
enrolled local hook-control service. Use the user or administrator-managed TOML
template in [the host setup guide](../HOST_HOOKS.md).

| Checkpoint | Coverage | Result |
| --- | --- | --- |
| `UserPromptSubmit` | `turn_entry` only | A denial blocks the submitted prompt before Codex processes it. |
| Pre-tool `PreToolUse` | Documented command-hook tool calls | A structured deny prevents the tool invocation. |
| `PostToolUse` | Correlated tool results | Outcome recording; Codex provides no separate post-tool failure event. |
| Subsequent model requests | Not observed | No `full_model_request` claim. |
| Route mode | Not provided by this adapter | Use a provider session where you own transport. |
| Subscription preservation | Yes | Decision mode leaves native Codex transport in place. |
| Deployment bypasses | Hook disabled, replaced, or not started | Outside the adapter boundary. |
| Unsupported paths | Hosted/specialized tool paths outside documented command hooks | Not covered by this adapter. |

The adapter excludes `mcp__atellagent__*` facade calls because the actual MCP
effect boundary governs those calls. Managed deployments can require managed
hooks and reject user/project/session/plugin hook configuration; see the host
guide.

When Codex also uses `atellagent-mcp-bridge`, the bridge is a credential-free
stdio adapter over the same owner-private hook-control socket. The control
runtime, not the bridge process, owns the enrolled identity and reviewed MCP
tool map.

Codex command hooks require an explicit `network.allow_unix_sockets` entry
for that exact socket path. Apply the bundled TOML template and fully restart
Codex after changing hooks, MCP servers, or socket permissions.
