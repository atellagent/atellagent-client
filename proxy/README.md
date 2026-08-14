# MCP compatibility proxies

## Purpose and placement

The MCP compatibility proxies bridge customer-owned MCP peers that need
supported negotiation compatibility. They are an `mcp_proxy` integration
surface, not the actual connected MCP tool effect boundary and not the
external-agent `agent.control` hook proxy.

## Public entry points and packaging

Install `atellagent-client[mcp]` plus the matching proxy extra. Use
`atellagent-agent-proxy` for an agent-facing stdio MCP facade and
`atellagent-tool-proxy` for a tool-facing target facade. Programmatic entry
points are `MCPAgentProxy`, `MCPToolProxy`, `MCPToolTarget`, and `MCPProxyTool`.

```bash
atellagent-agent-proxy --config proxy.yaml
```

## Failure semantics and exclusions

Negotiation ambiguity, invalid responses, and unsupported peer revisions fail
closed. A proxy only governs traffic that traverses it; it is not a claim that
an independently reachable target is protected. It is not a provider route
facade and does not add model-governance coverage. For actual tool enforcement,
use a connected MCP boundary or a local action gate as appropriate.
