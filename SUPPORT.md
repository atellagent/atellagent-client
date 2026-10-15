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
  continue the saved request when approval or delivery was interrupted. If the
  identity can no longer authenticate because its key is lost or its certificate
  is expired or revoked, ask your administrator to revoke the old identity and
  approve a replacement through the normal setup flow. Signing in again does not
  repair machine credentials. See [device recovery](docs/LOCAL_SERVICE.md#remove-local-enrollment-or-recover-a-device).
- Do not attach YAML containing tokens, certificate/key files, delivery
  capabilities, or customer payloads to support requests.

## Desktop and device troubleshooting

- Pending approval is not a startup failure. Continue the saved request after
  administrator review; do not submit another identity to restart the wait.
- Native connection approval and owner consent are separate. A running controller
  is not proof that the host has loaded its hooks. Review/save the generated hook
  settings and verify a real governed action in the host.
- Discovery reads current-user declarations. A detected agent or MCP entry is
  not proof of a healthy, approved connection. Check reporting freshness in
  Organization → Discovery and the exact integration in Runtimes & Integrations.
- Browser logout does not normally stop approved runtimes. Check current
  membership, revocation, configured authorization expiry and machine credential
  status before repeating sign-in.
- Use [the desktop guide](docs/DESKTOP_APP.md) for downloads, MCP setup and manual
  updates. Report the app version and sanitized diagnostic details, not local
  credentials or host configuration contents.

## Portable-image troubleshooting

- Confirm the exact manifest digest, client version, and image platform before
  opening a support request. Do not send a registry token, identity volume, or
  generated certificate material.
- The base image has no customer handler or optional provider SDK. Use a pinned
  derived image or a read-only customer-code mount for those components.
- The image exposes no port. A local host-hook socket is generally a native
  desktop deployment concern; Docker Desktop does not provide its user-owned
  Unix-socket boundary to Linux containers.
