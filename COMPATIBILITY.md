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
