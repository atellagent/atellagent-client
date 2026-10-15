# Codex coverage

Atellagent supports Codex through synchronous local `command` hooks and an
enrolled local hook-control service. Use the user or administrator-managed TOML
template in [the host setup guide](../HOST_HOOKS.md).

| Checkpoint | Coverage | Result |
| --- | --- | --- |
| `UserPromptSubmit` | `turn_entry` only | A denial blocks the submitted prompt before Codex processes it. |
| Pre-tool `PreToolUse` | Documented command-hook tool calls | A structured deny prevents the tool invocation. |
| `PostToolUse` | Correlated results when Codex emits the callback | This adapter records outcome metadata only; it does not inspect, redact, or gate the raw result. Codex does not guarantee a callback for every completed tool call or provide a separate post-tool failure event. Missing callbacks remain unreported outcomes. |
| Subsequent model requests | Not observed | No `full_model_request` claim. |
| Route mode | Not provided by this adapter | Use a provider session where you own transport. |
| Subscription preservation | Yes | Decision mode leaves native Codex transport in place. |
| Deployment bypasses | Hook disabled, replaced, or not started | Outside the adapter boundary. |
| Unsupported paths | Hosted/specialized tool paths outside documented command hooks | Not covered by this adapter. |
| Direct public Atellagent MCP | Separate public product route | It is not the local bridge integration. |
| Local `atellagent-mcp-bridge` | Local MCP route | The bridge connects Codex to the configured local control service. |
| Subagents | No inherited full-model coverage claim | This adapter governs only hook-visible prompt and tool events. |
| Tasks/background execution | No coverage claim | A turn-level hook does not claim model-traffic interception. |

Hook-visible MCP calls are not exempted by tool name. An MCP destination may
apply its own enforcement. Atellagent can avoid a duplicate Semantic Review
for a supported hook-visible call to its governed MCP route. This does not
deduplicate arbitrary external MCP servers or bypass other applicable policies.
Managed deployments
can require managed hooks and reject user/project/session/plugin hook
configuration; see the host guide.

Exact Codex `update_goal` calls with `status: blocked`, `status: paused`, or
`status: complete` use normal pre- and post-tool control when available, so
policy can govern them. Quota admission, authorization, native profile/adapter
coverage, technical holds, control outages and directive verification failures
cannot trap those
exact terminal transitions. Authored policy refusals and verified directive
denials remain blocking; malformed hook events and ordinary tools do not gain
this recovery exception.
Other goal updates remain governed by the usual preflight. A user clicking
Pause uses Codex's own control, not this tool exception.

Register the local bridge under the fixed `atellagent` server name. Its tools
come from the assigned catalog; the governed MCP boundary makes the typed tool
decision. Supported calls to the public Atellagent connector can also receive
hook checks without a local bridge. Other MCP calls are evaluated against the
host's available action coverage, not automatically trusted or denied merely
because they use MCP. In Enforce + Strict, unrecognized actions are denied;
recognized host surfaces do not imply typed tool-policy coverage. Check the
runtime's action inventory before enabling Strict.

This matrix is intentionally behavior- rather than version-claim based. Before
deployment, exercise the exact Codex release and configuration with a prompt,
a local function tool, an MCP bridge tool, a direct public MCP tool, a
subagent, and any enabled hosted/background task. A failed or absent hook is a
denial for an adapter-visible action, but it is not evidence about an
unsupported path. OpenAI documents that hosted tools and some specialized tool
paths can bypass the local function-tool hook path.

Outcome-only coverage is a limit of this integration, not a claim that the
host cannot control result delivery. OpenAI documents post-tool blocking
feedback, but this adapter does not implement that control or send the raw
tool result for inspection. A completed tool's side effects cannot be undone
by a post-tool hook. See [OpenAI's hook contract](https://learn.chatgpt.com/docs/hooks).

When Codex also uses `atellagent-mcp-bridge`, configure the bundled local bridge
in Codex's `config.toml`. The bridge connects to the same owner-private local
control endpoint. Complete device approval, connection approval and owner consent
through the [local service setup](../LOCAL_SERVICE.md) before starting the
controller. Routine controller starts and bridge calls use the approved machine
credentials and durable owner delegation; they do not initiate browser sign-in.
Management sign-in is used for setup and consent, separately from runtime access.

The macOS/Linux TOML template allowlists the exact Unix-socket path in Codex's
sandbox configuration. Windows uses a current-user named pipe and does not add
a Unix-socket allowance. The hook commands invoke the adapter directly; they do
not nest another `codex sandbox` process. Apply the matching bundled template
and fully restart Codex after changing hooks, MCP servers, or endpoint permissions.

Keep the template's command-hook timeout. It is a maximum wait, not expected
per-call latency.
