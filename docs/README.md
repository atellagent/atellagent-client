# Atellagent Client documentation

Choose the guide that matches where Atellagent joins your application. These
guides describe public client contracts only.

| I need to… | Start here |
| --- | --- |
| Embed governance in Python I own | [SDK](../sdk/README.md) |
| Run an outbound connected participant or private bridge | [Connected runtime](../connected/README.md) |
| Govern Claude Code or Codex | [External coding hosts](HOST_HOOKS.md) |
| Use OpenAI, Google, or Anthropic provider SDKs | [Provider sessions](../integrations/providers/README.md) |
| Govern a customer-owned tool boundary | [Local governance](../governance/README.md) and [PEP](../pep/README.md) |
| Use a compatibility MCP proxy | [MCP proxies](../proxy/README.md) |
| Build an agent, model, tool, workflow, or channel adapter | [Integrations](../integrations/README.md) |
| Run the portable client image | [Docker](../docker/README.md) |

## Important distinctions

- Placement is `hosted`, `connected`, or `external_resource`.
- Integration surfaces are `sdk`, `bridge`, `hook`, `provider_proxy`, and
  `mcp_proxy`.
- Model governance is `decision` or `route`—it is independent of integration
  surface.
- Identity is `boundary_identity_only` or `federated_agent_identity`.

Workflow membership is optional for model and tool policy. A workflow can add
context to a supported call, but it is not required for the client to request a
decision or enforce an action.

## Host coverage

- [Claude Code](hosts/claude-code.md) and [Codex](hosts/codex.md) are supported
  through local command hooks.
- [Anthropic Messages route facade](hosts/claude-code-route-mode.md) documents
  the supported non-streaming API subset; it is not a Claude Code route-mode
  deployment.
- [OpenAI Responses route facade](hosts/codex-route-mode.md) documents the
  equivalent non-streaming API subset; it is not a Codex deployment.
- [Gemini CLI](hosts/gemini-cli.md) supports `BeforeModel` and `BeforeTool`
  decision-mode hooks; `AfterModel` is not supported.
- [Claude Cowork](hosts/claude-cowork.md) is not supported.

See [Building custom integrations](BUILDING_CUSTOM_INTEGRATIONS.md) when your
runtime is not one of the supported adapters.
