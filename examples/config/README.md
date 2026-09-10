# Configuration examples

These examples describe different deployment boundaries. They are not
interchangeable.

| Example | Use it for | Identity |
| --- | --- | --- |
| `local-mcp-bridge.yaml` | A local Codex/host MCP adapter that exposes the Agent Proxy's centrally assigned external MCP catalog. Run it with `atellagent-mcp-bridge`. | No credentials or enrollment; it uses the Agent Proxy's private socket. |
| `tool-proxy.yaml` | A customer-local MCP server protected by an enrolled connected Tool Proxy. Run the generated configuration with `atellagent-cli`. | Its own Tool Proxy service account and certificate. |
| `agent_bridge.yaml` | A generic enrolled agent bridge. | Its own agent-boundary service account and certificate. |
| `codex-hooks.user.toml` | Codex hooks on Linux. | Credential-free adapter over the enrolled Agent Proxy's private socket. |
| `codex-hooks.macos.toml` | Codex hooks on macOS. | Credential-free adapter over the enrolled Agent Proxy's private socket. |

The dashboard-generated configuration is authoritative for any enrolled
runtime. These files are structural examples only: replace all placeholders
with dashboard-provided values and do not copy credentials between boundaries.
