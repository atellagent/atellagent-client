# Connected model and filter adapters

## Purpose

Use these contracts when a customer operates a connected model or filter
implementation. `ModelRuntimeHandler` and `FilterRuntimeHandler` describe the
public handler contract; `OllamaModelRuntimeHandler` and
`HuggingFaceTextClassificationFilter` are optional customer-side implementations.

## Packaging and ownership

Install only the matching optional extra. The customer owns provider endpoints,
model assets, and credentials. Mount a handler on a `ConnectedSDKRuntime` with
the corresponding connected helper.

```python
from atellagent_client.integrations.models import ModelRuntimeHandlerBase

class CustomerModel(ModelRuntimeHandlerBase):
    async def invoke_model(self, request):
        return {"content": "customer result"}
```

## Coverage and exclusions

These adapters handle their documented connected deliveries. They do not turn a
filter alert into a model decision, rewrite an external host response, or create
a provider-route fallback. Validate input/result contracts and let malformed or
failed calls surface through the connected runtime.
