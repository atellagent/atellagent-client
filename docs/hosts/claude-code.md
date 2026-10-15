# Claude Code coverage

Atellagent supports Claude Code through local `command` hooks and an enrolled
local hook-control service. Follow [the host setup guide](../HOST_HOOKS.md) and
use the committed user or managed configuration template.

| Checkpoint | Coverage | Result |
| --- | --- | --- |
| `UserPromptSubmit` | `turn_entry` only | A denial blocks the submitted prompt before Claude Code processes it. |
| Pre-tool `PreToolUse` | Supported hook-visible tool calls | A structured deny prevents the tool invocation. |
| `PostToolUse` | Correlated successful tool calls | Outcome recording. |
| `PostToolUseFailure` | Correlated failed tool calls | Failure outcome recording. |
| Subsequent model requests | Not observed | No `full_model_request` claim. |
| Route mode | Not supported | Claude Code requires event streaming; see the [non-streaming route facade](claude-code-route-mode.md). |
| Subscription preservation | Yes | Decision mode leaves native Claude Code transport in place. |
| Deployment bypasses | Hook disabled, changed, or not started | Outside the adapter boundary. |
| Unsupported paths | Claude Cowork and paths that do not emit supported command hooks | Not covered by this page. |

Hook-visible MCP calls, including Atellagent calls, receive normal hook checks.
Claude Code's `/goal` completion is evaluated by its own session-scoped Stop
hook, not by an `update_goal` tool call. This adapter does not register a Stop
or TaskCompleted hook, so no matching goal lifecycle exception is needed here.
Register the local bridge as `atellagent`. Its tools come from the assigned
catalog; the governed MCP boundary makes the typed tool decision. Other MCP
calls are evaluated against the host's available action coverage, not
automatically trusted or denied merely because they use MCP. In Enforce +
Strict, unrecognized actions are denied; recognized host surfaces do not
imply typed tool-policy coverage. Check the runtime's action inventory before
enabling Strict. Deduplication is not provided for this host. Command-hook
failures return an exit-2 denial before the configured
host timeout when the command starts. Keep the 20-second host timeout in the
template above the adapter's 19-second deadline: Claude Code can continue a
tool through its normal permission flow if the host times out the command hook
first. Claude Code's own failure behavior remains a host boundary.

This page covers Claude Code only. It does not claim Claude Cowork coverage.
