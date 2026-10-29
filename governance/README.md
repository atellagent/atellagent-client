# Local governance

## Purpose and placement

`RuntimeActionGate` is the public local enforcement boundary for a
customer-owned action. Use it beside the effect it protects, not merely beside
the model or UI that requested the effect.

## Packaging, configuration, and entry points

The core client is sufficient for local manifest enforcement. A local manifest
declares only the actions and bounded facts that the customer boundary will
enforce:

```yaml
schema_version: v1
mode: enforce
actions:
  file.write:
    writable_roots: [/workspace]
    max_bytes: 262144
```

Build a gate from that manifest before the protected effect:

```python
from atellagent_client.governance import RuntimeActionGate

gate = RuntimeActionGate.from_local_manifest("local_guardrails.yaml")
await gate.enforce(
    action="file.write",
    integration_type="mcp",
    correlation_id="request-123",
    facts={"path": "/workspace/out.txt", "bytes": 128},
)
```

`ActionDenied` is the stable failure type. Local manifests select documented
`enforce` or `observe` behavior; they do not create a managed-service policy
implementation in the client.

## Coverage, failures, and exclusions

The gate protects only actions that actually traverse it. A tool, filesystem,
database, or other target independently reachable outside the protected
boundary still needs its own authorization. The gate is not a model proxy,
workflow engine, provider SDK bridge, or identity resolver. See [PEP](../pep/README.md)
for the lower-level directive and local-guardrail primitives.
