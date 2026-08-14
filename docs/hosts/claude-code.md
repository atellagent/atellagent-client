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
| Unsupported paths | Claude Cowork and non-command-hook paths | Not covered by this page. |

Calls through `mcp__atellagent__*` are handled at the MCP effect boundary and
are not preflighted a second time by the hook adapter. Command-hook failures
return an exit-2 denial before the configured host timeout when the command
starts; Claude Code's own failure behavior remains a host boundary.

This page covers Claude Code only. It does not claim Claude Cowork coverage.
