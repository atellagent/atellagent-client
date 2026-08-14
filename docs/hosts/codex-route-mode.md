# OpenAI Responses route facade — non-streaming

This optional loopback facade implements a supported non-streaming subset of
the OpenAI Responses API through Atellagent route governance. It is not a Codex
custom-provider deployment: Codex requires event streaming, and this facade
rejects `stream: true`.

## Before you start

Provision and enroll the standard boundary-only connected `agent` with only
the `agent.control` capability. Create a random local capability token in an
absolute, owner-only file. The token authenticates a local API client to the
facade; it is not an Atellagent service-account credential, OpenAI API key, or
ChatGPT subscription credential.

Start the facade on loopback only:

```bash
atellagent-openai-facade ./route-agent.yaml \
  --token-file /absolute/private/atellagent-route-token \
  --listen 127.0.0.1:8788
```

A non-streaming OpenAI-compatible test client may use
`http://127.0.0.1:8788/v1` as its base URL and the local capability token as
its API key. This facade is not a supported Codex endpoint.

## Coverage and limits

| Contract | Coverage |
| --- | --- |
| Model input | `full_model_request` route governance before provider dispatch. |
| Responses API | Supported non-streaming text input, prior function calls/results, function tools, tool choice, sampling, metadata, and usage. |
| Tool effect | Not executed by this facade. Retain a separate host-tool hook or MCP effect boundary. |
| Codex deployment | Not supported. This facade does not provide event streaming. |
| Native subscription | Not preserved. Route mode uses the configured Atellagent provider route and billing. |
| `stream: true` | Rejected with an OpenAI-compatible invalid-request error. No events are synthesized. |
| Unsupported features | Rejected rather than silently dropped, including built-in tools, reasoning, response storage, and content outside the documented subset. |

The facade never forwards the local token, an OpenAI API key, or a consumer
subscription credential. Policy denial occurs before provider dispatch. Route
failure does not fall back to a native-provider request.

Streaming is not supported by this facade; it must not be represented as Codex
support.
