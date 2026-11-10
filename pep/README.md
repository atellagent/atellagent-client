# Policy enforcement point primitives

## Purpose and placement

The PEP package supplies bounded local action checks and verification of opaque
remote control directives. Use it when building a customer-owned boundary that
needs a documented enforcement primitive rather than the higher-level
`RuntimeActionGate` convenience API.

## Public entry points and ownership

Use `ActionIntent`, `IntegrationCapability`, `load_local_guardrail_manifest`,
`evaluate_action`, `evaluate_connected_action`, or `evaluate_controlled_action`
from `atellagent_client.pep`. A connected directive verifier is configured from
the enrolled boundary; private keys and provider credentials remain outside the
PEP call.

A signed directive must match the local action, arguments, host context, decision
and correlation. Tenant, execution and workspace claims must also match the
local intent exactly: a missing or null claim cannot satisfy a specified local
scope. A scope remains optional when neither side specifies it. Signature
verification and these bindings do not independently establish that a stored
policy or delegation is still current; the Gateway owns release revalidation.

## Failure semantics and exclusions

Malformed local manifests, invalid directives, missing required capabilities,
and denied actions fail closed through the documented error/decision contract.
The PEP does not evaluate managed policy, resolve an external identity, select a
provider, or run a workflow. Its role is local enforcement and validation only.

For a ready-to-use action gate, see [Local governance](../governance/README.md).
