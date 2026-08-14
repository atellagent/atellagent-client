# Anthropic Messages route facade — non-streaming

This optional integration implements a supported non-streaming subset of the
Anthropic Messages API through a loopback-only facade. It is not a Claude Code
deployment: Claude Code requires event streaming, and this facade rejects
`stream: true`. The separate Claude Code
command-hook adapter remains the supported host integration; it provides
`turn_entry` and host-visible tool checkpoints.

## Before you start

Provision and enroll the standard boundary-only connected `agent` with only
the `agent.control` capability. Create a random local capability token in an
absolute, owner-only file. The token authenticates Claude Code to the local
facade; it is not an Atellagent service-account credential, provider API key,
or Claude subscription credential.

Start the facade on loopback only:

```bash
atellagent-anthropic-facade ./route-agent.yaml \
  --token-file /absolute/private/atellagent-route-token \
  --listen 127.0.0.1:8787
```

Use a non-streaming Anthropic-compatible test client in the same local-user
boundary:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
export ANTHROPIC_AUTH_TOKEN="$(</absolute/private/atellagent-route-token)"
```

The facade accepts that exact local capability value in `Authorization` or
`x-api-key`, compares it locally, and never forwards either header. The
enrolled facade process alone authenticates to Atellagent and uses the
configured route. Keep the token file and the host configuration owner-only.

## Coverage and limits

| Contract | Coverage |
| --- | --- |
| Model input | `full_model_request` route governance before provider dispatch. |
| Messages API | Supported non-streaming text, client tool use/results, tool definitions, tool choice, sampling, stop sequences, and usage. |
| Tool effect | Not executed by this facade. Retain Claude Code pre-tool hooks or an actual MCP effect boundary. |
| Claude Code deployment | Not supported. This facade does not provide event streaming. |
| Native subscription | Not preserved. Route mode uses the configured Atellagent provider route and billing. |
| `stream: true` | Rejected with an Anthropic-compatible invalid-request error. No token events are synthesized. |
| Unsupported content/features | Rejected rather than silently dropped, including content blocks or request fields outside the documented subset. |
| Deployment bypass | A host not configured to use the loopback facade remains outside this boundary. |

The route response returns standard Anthropic text and `tool_use` blocks. A
later Claude Code tool execution is governed only if it traverses its own
documented PEP or hook boundary.

## Failures and diagnostics

Invalid local credentials return an Anthropic authentication error. Policy
denials return a permission error before provider dispatch. Route transport
failures return an API error; the facade does not retry through Claude Code's
native provider path. It returns customer-safe errors only—do not add provider
keys, enrolled credentials, request content, or local capability tokens to a
support request.

Streaming is not supported by this facade.
