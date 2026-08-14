Copyright (c) 2026 Atellagent, Inc. All rights reserved. See [LICENSE.md](http://license.md/).

# Compatibility Policy

The package follows semantic versioning for documented public imports and
versioned request contracts. A major version may remove or alter a public API.
Minor and patch releases may add capabilities and fix defects without changing
the documented contract.

Configuration uses direct cutovers: removed fields and module paths are
rejected rather than silently normalized or retained through compatibility
aliases. Compatibility commitments apply only to the documented public imports
and versioned contracts.

The supported customer extension points are listed in
[`docs/BUILDING_CUSTOM_INTEGRATIONS.md`](docs/BUILDING_CUSTOM_INTEGRATIONS.md).
Implementations that use only those contracts remain compatible across minor and
patch releases; a major version may make a documented breaking change.

Provider-native tool adapters are available for OpenAI, Google, and Anthropic.

The current release supports CPython 3.11 only. A Python version is added to
the supported range only after its dependency lock, package build, complete
test suite, Docker smoke test, and documented examples pass in CI.

## Portable image

The portable image supports `linux/amd64` and `linux/arm64` manifests for the
documented core client runtime. A release digest is immutable; deploy and roll
back by digest. Mutable image tags are convenience aliases and are not a
compatibility or production-pinning contract. Customer provider SDKs, adapters,
and handlers belong in a customer-derived image pinned to the public base
digest.
