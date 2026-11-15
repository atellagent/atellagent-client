Copyright (c) 2026 Atellagent, Inc. All rights reserved. See [LICENSE.md](http://license.md/).

# Compatibility Policy

The package follows semantic versioning for documented public imports and
versioned request contracts. A major version may remove or alter a public API.
Minor and patch releases may add capabilities and fix defects without changing
the documented contract.

Configuration accepts only documented fields and module paths. Unsupported
values are rejected rather than silently normalized or accepted through
compatibility aliases. Compatibility commitments apply only to the documented
public imports and versioned contracts.

The supported customer extension points are listed in
[`docs/BUILDING_CUSTOM_INTEGRATIONS.md`](docs/BUILDING_CUSTOM_INTEGRATIONS.md).
Implementations that use only those contracts remain compatible across minor and
patch releases; a major version may make a documented breaking change.

Provider-native tool adapters are available for OpenAI, Google, and Anthropic.

## 0.9.5 MCP adapter cutover

This release consolidates MCP adapters into `atellagent_client.proxy`. Import
`MCPClient` and `MCPTargetError` from that package. The old connected MCP modules
and exports have been removed; update imports when installing 0.9.5. There are
no aliases for the removed paths.

Upstream servers must support revision `2026-07-28` over stdio or Streamable
HTTP with JSON or SSE responses. The old HTTP+SSE endpoint transport is removed.
The native agent-facing facade has its separately declared supported revisions;
see the [adapter guide](proxy/README.md). No error-triggered protocol downgrade
or alternate execution path is used.

Ordinary MCP targets do not need Atellagent's durable-idempotency extension.
Accepted consequential calls use single dispatch. A lost result after invocation
can mean an unknown outcome: reconcile the original action rather than submit
the operation again. Verified target-native idempotency remains optional.

## Python support

The current release supports CPython 3.11 only. A Python version is added to
the supported range only after its dependency lock, package build, complete
test suite, Docker smoke test, and documented examples pass in CI.

## Installation trust prerequisites

Use an up-to-date OS trust store and a supported, patched Python package
installer. For the October 2026 emSign incident, pip 26.2.2 or later and certifi
2026.10.19 or later contain the certificate removal; upgrading certifi alone
does not repair an older pip's bundled trust store. See the
[Python security advisory](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576).

Client connections using Python's default TLS context also use the host's OS
trust store unless an explicit CA bundle is configured. Installing this package
does not update that store. Follow your OS vendor's trust updates and preserve
any private CAs your organization requires; keep TLS verification enabled.

## Portable image

The portable image supports `linux/amd64` and `linux/arm64` manifests for the
documented core client runtime. A release digest is immutable; deploy and roll
back by digest. Mutable image tags are convenience aliases and are not a
compatibility or production-pinning contract. Customer provider SDKs, adapters,
and handlers belong in a customer-derived image pinned to the public base
digest.
