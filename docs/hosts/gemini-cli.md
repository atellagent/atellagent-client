# Gemini CLI coverage

Atellagent supports Gemini CLI through synchronous local command hooks and an
enrolled local hook-control service. Use
`examples/config/gemini-cli-hooks.user.json` with the common
[host-hook guide](../HOST_HOOKS.md).

| Checkpoint | Coverage | Result |
| --- | --- | --- |
| `BeforeModel` | Hook-visible model, messages, generation configuration, and tool configuration | A denial blocks the exact observed LLM request before dispatch. |
| `BeforeTool` | Hook-visible tool name and object arguments | A denial prevents tool execution. |
| `AfterModel` | Not supported | No response inspection, rewriting, or redaction. |
| Tool outcome | Not supported | No stable host tool-call identifier for correlated postflight. |
| Subscription preservation | Yes | Decision mode leaves Gemini CLI's native provider transport in place. |

The adapter uses the enrolled Atellagent identity only; it receives no provider
credentials and does not alter Gemini model requests or responses. It binds its
decision to the hook-visible request; unavailable or malformed local control
blocks the request.
