Copyright (c) 2026 Atellagent, Inc. All rights reserved. See [LICENSE.md](http://license.md/).

# Support Policy

Support requests for the public client contract go to `support@atellagent.com`.
Include the package version, integration type, correlation ID, and sanitized
error output. Do not send certificate material, bearer tokens, or customer data.

Supported surfaces are documented in the README. Undocumented underscored
modules and managed-service behavior are not public support contracts.

## Connected-runtime troubleshooting

- A startup configuration error for `local_manifest` means the manifest is
  absent, malformed, selected for a non-MCP integration, or paired with an
  unsupported key. The client never changes to managed policy automatically.
- An MCP bridge discovery error means the local target did not advertise the
  required stateless MCP `2026-07-28` revision. Older MCP revisions are not
  supported.
- An `ActionDenied` reason such as `action_not_configured`,
  `path_not_writable`, or `bytes_limit_exceeded` is a local enforcement
  decision. Use `mode: observe` only for an intentional evaluation period.
- TLS verification uses the operating-system trust store. There is no gateway
  custom-CA setting. `deployment.upstream_ca_path`, if present, is only for a
  customer-local bridge target.
- Repeated `401` responses trigger one OAuth refresh; persistent failures mean
  the client certificate, OAuth client, account, or integration is no longer
  accepted.
- A rotation deadline, issuer error, or post-activation reconnect error appears
  in participant logs and the dashboard. Keep staged credentials private and
  use a new administrator-issued recovery enrollment when the old identity can
  no longer authenticate.
- Do not attach YAML containing tokens, certificate/key files, delivery
  capabilities, or customer payloads to support requests.
