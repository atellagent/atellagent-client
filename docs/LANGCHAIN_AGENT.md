# LangChain agent example

This example uses a real LangChain agent loop, an Atellagent-routed model and
one MCP tool. It uses the public SDK; it does not require a desktop app,
Codex/Claude hooks, or a provider API key in the customer process.

Install the client and LangChain in your Python environment:

```sh
pip install atellagent-client 'langchain>=1,<2'
```

## Setup

Use the [workload setup flow](../connected/README.md) to request an SDK agent:

```sh
atellagent-cli workload request \
  --state-directory /absolute/private/workload-state \
  --platform-url https://atellagent.com --name "LangChain agent" \
  --type agent --packaging sdk --capability agent.process \
  --identity-mode boundary_identity_only
```

Complete browser sign-in, choose your organization, and obtain administrator
approval. Retain the private state directory and printed request ID. If the
approval wait ends, use `workload resume` with that same ID. The agent must
be allowed to invoke the selected model and call the selected MCP integration.
Do not reuse another agent's identity or certificates.

Use an approved model integration with its configured provider credentials and
function-call support. A standalone machine does not inherit the provisioning
user's personal provider key. For a registered model integration, select provider
`custom` and its advertised model capability.
Use `--reasoning-effort` when the selected model requires an explicit setting.
For GPT-6 Luna over an OpenAI-compatible Chat Completions connection, use
`--reasoning-effort none` for function calls.

Configure a reachable, registered MCP tool with the input schema
`{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}`.
The example calls that tool with `text`; change the example's tool function if
your target has a different schema. The target binding is a routing reference,
not permission to call the target.

Run one turn:

```sh
python -m atellagent_client.examples.agent.langchain_agent_quickstart \
  --state-directory /absolute/private/workload-state \
  --request-id YOUR_APPROVED_REQUEST_ID \
  --provider custom --model YOUR_REGISTERED_MODEL_CAPABILITY \
  --target-binding YOUR_APPROVED_MCP_TARGET --tool-name echo
```

The model can request `governed_echo`; its callback sends the real arguments
and the framework's tool-call ID through the authenticated MCP SDK call.
The call ID is not a model-editable tool argument. Model, authentication, transport and
policy errors propagate. No provider or local tool fallback is configured.
The agent supplies its own messages; these calls do not create or use platform
conversation memory. A policy requiring platform-managed context rejects this mode.

## Workflow handler

The same adapter can run as a connected SDK agent selected by a workflow. Configure
the approved request with this handler, then run its receiver:

```sh
atellagent-cli workload configure YOUR_APPROVED_REQUEST_ID \
  --state-directory /absolute/private/workload-state \
  --handler atellagent_client.examples.agent.langchain_connected_handler:handle \
  --target-idempotent
export ATELLAGENT_EXAMPLE_MODEL=YOUR_REGISTERED_MODEL_CAPABILITY
export ATELLAGENT_EXAMPLE_MCP_TARGET=YOUR_APPROVED_MCP_TARGET
export ATELLAGENT_EXAMPLE_CONTEXT_MODE=platform
atellagent-cli workload run YOUR_APPROVED_REQUEST_ID --state-directory /absolute/private/workload-state
```

For a model requiring an explicit reasoning setting, also set
`ATELLAGENT_EXAMPLE_REASONING_EFFORT`. Configure the workflow's model settings
consistently; workflow-authorized settings take precedence over handler options.

Select this integration in a Chat-enabled workflow for platform conversations.
`platform` loads the authorized conversation history and adds the current turn;
prior messages supplied by the handler are not used as platform history. The
agent's current-turn assistant/tool messages are still customer-produced input,
not independent proof that a tool executed. Use `caller` for caller-managed
context; a policy requiring platform context rejects it. Workflow user authority
comes from the delivery, not from a user ID added to the handler payload.
The example has no local file/shell tools or cross-turn in-process checkpoint.
It reports a runtime-start observation through the same SDK event API before
running the agent. Event reporting failures stop the turn. Runtime observations
are customer-reported facts, not authorization or proof of tool execution.

## Coverage limits

Only the model calls and MCP calls made through these functions are covered.
Agent registration alone does not govern arbitrary local functions, file
access or shell commands. Runtime identity is the approved machine/agent
identity, not the human who completed setup.

The small model adapter supports asynchronous, non-streaming text conversations
and function calls. It is example code, not a general LangChain provider:
multimodal inputs, streaming, structured output and provider-specific options
are outside its scope. Framework telemetry services are not needed.
