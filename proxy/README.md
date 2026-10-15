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
`control_socket`. Start the enrolled `agent.control` runtime on that endpoint;
it discovers accessible servers and owns the exact tool-to-target map.
The bridge itself holds no service-account credentials, target API key, OAuth
client secret, or refresh token. Atellagent owns the policy decision and, when
it manages the external target, the credentialed outbound call. MCP result
content is relayed without converting it to a text blob.

The local bridge advertises four tools: `atellagent_list_servers`,
`atellagent_list_tools`, `atellagent_describe_tool`, and `atellagent_call_tool`.
List servers first, list or search a selected server, then request a tool's full
schema and call it with the returned server and tool IDs. Lists are paginated
(up to 20 entries); only selected schemas are returned. Discovery does not grant
access: permissions and the real tool arguments are checked on every call.

Use [`examples/config/local-mcp-bridge.yaml`](../examples/config/local-mcp-bridge.yaml)
for this credential-free, agent-side adapter. It is not a Tool Proxy
configuration.

A connected MCP boundary protecting a customer-local server uses the
[managed workload flow](../connected/README.md) and its own service account.
It is not run by Codex and must not share the Agent Proxy's credentials or
local socket. Choose the approved MCP bridge profile and configure its local
target separately from its managed identity.

`atellagent-agent-proxy` is the direct-SDK option for an approved agent workload.
Complete attended workload setup first, then reuse its saved private state and
request ID. It does not initiate browser login or use a local control socket:

```bash
atellagent-agent-proxy --state-directory /absolute/private/workload-state \
  --request-id YOUR_APPROVED_REQUEST_ID --tools /absolute/path/tools.yaml
```

The local catalog contains only tool presentation and target mappings:

```yaml
tools:
  - name: lookup
    description: Lookup a record
    input_schema:
      type: object
      properties:
        id: {type: string}
    target_binding: YOUR_TARGET_BINDING
    target_tool_name: find
```

Identity, certificates and endpoints come from approved managed state, not this
catalog. Pending or denied setup does not start the proxy. Calls act under the
approved workload's machine identity, not its setup operator's human session.
Use the local control bridge for native user-delegated calls. These are distinct
deployment modes, not fallbacks. See the [workload setup guide](../connected/README.md).

## Failure semantics and exclusions

Negotiation ambiguity, invalid responses, and unsupported peer revisions fail
closed. A proxy only governs traffic that traverses it; it is not a claim that
an independently reachable target is protected. It is not a provider route
facade and does not add model-governance coverage. For actual tool enforcement,
use a connected MCP boundary or a local action gate as appropriate.
