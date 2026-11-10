# Workflow participants

## Purpose

Use a workflow participant when a customer-owned workflow runtime receives
connected workflow deliveries. Public contracts include
`WorkflowParticipantHandler`, `WorkflowParticipantActions`, and the typed
compile, execution, resume, and cancel requests. `LangGraphWorkflowParticipant`
is the optional LangGraph adapter.

## Packaging and operation

Install `atellagent-client[langgraph]` only when using that adapter. Mount the
handler with `mount_workflow_handler` on a connected runtime. Customer workflow
state and provider credentials remain in the customer environment.

```python
from atellagent_client.integrations.workflows import WorkflowParticipantHandlerBase

class CustomerWorkflow(WorkflowParticipantHandlerBase):
    pass
```

## Policy context and exclusions

A workflow can supply context to a supported model or action call, but policy
also applies when no workflow is involved. This package does not define service
accounts, agent principals, or a separate policy mode. Invalid lifecycle
requests and failed customer executions surface through the documented
participant contract.
