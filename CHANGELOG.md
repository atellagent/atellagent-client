Copyright (c) 2026 Atellagent, Inc. All rights reserved. See [LICENSE.md](http://license.md/).

# Changelog

## 0.9.5

- Connect ordinary MCP servers through one stdio and Streamable HTTP adapter,
  including JSON and SSE responses, without a proprietary idempotency extension.
- Preserve uncertain outcomes after an accepted tool call instead of
  automatically repeating the operation. Retry its original result commit.
- Add an original-tool resource facade with per-request native caller context.
- Reuse bounded controller HTTP connections and recover interrupted accepted
  acknowledgments and result commits.
- Move MCP adapter imports to `atellagent_client.proxy` and remove the old
  connected MCP modules and HTTP+SSE endpoint transport.

## 0.9.4

- Ship the native controller and background service in the Mac and Windows
  desktop installers.
- Keep standalone workloads, the SDK, and public hook adapters in the client
  package.

## 0.9.3

- Initial release.
