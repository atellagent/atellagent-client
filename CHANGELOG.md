Copyright (c) 2026 Atellagent, Inc. All rights reserved. See [LICENSE.md](http://license.md/).

# Changelog

## Unreleased

- Pin the patched certifi CA bundle for GHSA-6q48-6c7w-9gw2 and refresh its verified container hash and license notice.
- Recover failed background supervisors with OS restart limits while preserving explicit stops and private service storage.
- Schedule certificate renewal before expiry and show retry warnings without blocking still-authorized connections.
- Retry transient connection establishment only for body-free onboarding reads within one deadline; preserve TLS and response failures.
- Return explicit Codex and Claude Code pre-tool denials for adapter and transport failures without exposing private diagnostics.
- Bind connected observations to their delivery and retry exact result commits without rerunning completed handlers.

## 0.9.2

- Remove the AAA cross-sign trust path for the compromised eMudhra CAs from the
  portable image. Reject all twelve published incident chains in an offline
  image-build check, preserving other public roots.
- Use patched outer pip for CI's isolated wheel installs, repair the derived
  image example and document host installation trust prerequisites.

## 0.9.1

- Corrected package-description links for PyPI and updated pip bootstrap to the
  hash-verified security release; removed the compromised emSign G3 root from
  container system trust and removed bundled ensurepip from the runtime.
- Added private native-host setup, discovery, credential selection, and governed
  hook configuration and execution.
- Aligned documented integrations with the public factory and client APIs.
- Updated locked cryptography and PyJWT dependencies and the pinned Python image
  base; added an inventory for bundled native components.
- Made CI install its test tools explicitly and corrected native-hook test
  isolation across concurrent calls and simulated platforms.

## 0.9.0

- Added native OpenAI, Google, and Anthropic function-tool adapters.
- Added MCP compatibility proxies for supported legacy revisions and
  `2026-07-28` peers.
- Added the connected runtime for CPython 3.11.
- Added certificate rotation and recovery enrollment support.
- Added an artifact-first, digest-pinned portable client image release contract.
