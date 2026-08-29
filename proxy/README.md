# MCP compatibility proxies

## Purpose and placement

The MCP compatibility proxies bridge customer-owned MCP peers that need
supported negotiation compatibility. They are an `mcp_proxy` integration
surface, not the actual connected MCP tool effect boundary and not the
external-agent `agent.control` hook proxy.

## Public entry points and packaging

Install `atellagent-client[mcp]` plus the matching proxy extra. Use
`atellagent-mcp-bridge` for a local agent-proxy stdio facade and
`atellagent-agent-proxy` for a direct-SDK stdio facade. `atellagent-tool-proxy`
is a tool-facing target facade. Programmatic entry points are `MCPAgentProxy`,
`MCPToolProxy`, `MCPToolTarget`, and `MCPProxyTool`.

```bash
atellagent-mcp-bridge --config bridge.yaml
```

`atellagent-mcp-bridge` requires a local-proxy configuration with an absolute
`control_socket`. Start the enrolled `agent.control` runtime with the same
configuration through `--mcp-bridge-config`; it fetches the assigned catalog
and owns the exact tool-to-target map.
The bridge itself holds no service-account credentials, target API key, OAuth
client secret, or refresh token. Atellagent owns the policy decision and, when
it manages the external target, the credentialed outbound call. MCP result
content is relayed without converting it to a text blob.

Use [`examples/config/local-mcp-bridge.yaml`](../examples/config/local-mcp-bridge.yaml)
for this credential-free, agent-side adapter. It is not a Tool Proxy
configuration.

An enrolled Tool Proxy that protects a customer-local MCP server instead uses
[`examples/config/tool-proxy.yaml`](../examples/config/tool-proxy.yaml) and
its own service account. It is not run by Codex and must not share the Agent
Proxy's credential or local socket.

`atellagent-agent-proxy` remains the explicit direct-SDK option. Its separate
configuration includes a client configuration path and it does not use a local
control socket. These are distinct deployment modes, not fallbacks.

## Failure semantics and exclusions

Negotiation ambiguity, invalid responses, and unsupported peer revisions fail
closed. A proxy only governs traffic that traverses it; it is not a claim that
an independently reachable target is protected. It is not a provider route
facade and does not add model-governance coverage. For actual tool enforcement,
use a connected MCP boundary or a local action gate as appropriate.
