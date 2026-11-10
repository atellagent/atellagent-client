# Configuration examples

These examples describe different deployment boundaries. They are not
interchangeable.

| Example | Use it for | Identity |
| --- | --- | --- |
| `local-mcp-bridge.yaml` | A local Codex/host MCP adapter that exposes the Agent Proxy's centrally assigned external MCP catalog. Run it with `atellagent-mcp-bridge`. | No credentials or enrollment; it uses the Agent Proxy's private socket. |
| `codex-hooks.user.toml` | Codex hooks on Linux. | Credential-free adapter over the enrolled Agent Proxy's private socket. |
| `codex-hooks.macos.toml` | Codex hooks on macOS. | Credential-free adapter over the enrolled Agent Proxy's private socket. |
| `codex-hooks.windows.toml` | Codex hooks on Windows. | Credential-free adapter over the enrolled Agent Proxy's current-user named pipe. |
| `claude-code-hooks.windows.json` | Claude Code hooks on Windows. | Credential-free adapter over the enrolled Agent Proxy's current-user named pipe. |
| `local-mcp-bridge.windows.yaml` | A local Windows Codex/host MCP adapter. | No credentials or enrollment; it uses the Agent Proxy's current-user named pipe. |

These files describe credential-free local adapters, host settings and local
guardrails—not service-account identity. Use the [device service guide](../../docs/LOCAL_SERVICE.md)
for native connections and [managed workload setup](../../connected/README.md)
for SDK/bridge deployments. Runtime identity and endpoints come from approved
managed state; do not copy credentials between boundaries. Preview generated
native hook settings before applying them rather than copying a placeholder
endpoint into your host settings.
Codex hook examples use `statusMessage` to describe each handler while it runs;
that field does not rename numbered entries in Codex's hook browser.
