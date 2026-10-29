# Local device service

One current-user service runs device reporting, independently approved native
controllers and selected local MCP bridges on macOS or Windows. A device can report discovery before any agent
connection is provisioned. Device identity, controller credentials and local
endpoints remain separate; a display name is not an authorization signal.

For app installation, connection controls and updates, use the
[desktop app guide](DESKTOP_APP.md). For device-associated MCP requests, local
target review and foreground execution, use the
[connected-runtime guide](../connected/README.md).

## Enroll the device

In the Mac or Windows desktop app, start device setup without a configuration
file or enrollment token. Sign in, choose your organization and name the device.
Your administrator reviews the request in Organization → Discovery. After
enrollment, start the service to report discovery; no agent connection is needed.
Request a supported agent connection or pick up an administrator assignment,
review its scope, and consent. Review/save its host hooks and follow any host
restart or new-chat instructions before relying on them.

The terminal follows the same approval flow:

Use your organization's trusted HTTPS deployment origin:

```bash
atellagent-cli setup device --platform-url https://atellagent.com --name "Work laptop"
atellagent-cli setup status --json
```

To request connections in the same browser sign-in, supply a local selections
file with `setup device --connections-file ./connections.json`. For example:

```json
{
  "native": [{"host": "codex", "name": "Work laptop Codex"}],
  "mcp": [{"name": "Local tools", "url": "http://127.0.0.1:8787/mcp", "command": "", "arguments": []}]
}
```

Choose at most eight connections. A stdio entry uses `command` and `arguments`
instead of `url`. These target settings stay local; selecting an entry does not
launch or contact it. Organization permissions still apply. Discovery supports
reviewing the requests together while recording separate decisions. Native owner
consent, hook setup and MCP target review remain separate steps.

Complete browser sign-in using the displayed link and code, then choose the
organization. An administrator approves the device request in Discovery. If the
command stops waiting, continue the saved request:

```bash
atellagent-cli setup resume
```

Resume retries unconfirmed selected-connection submissions before continuing
device enrollment. `atellagent-cli setup requests submit` explicitly retries the
retained connection requests. A retry may need browser sign-in if the previous
management interaction has ended. Resume retains the original request and private key. Do not create a replacement
request to recover an interrupted approval or credential delivery. An approved
device uses its machine credentials for background reporting; starting the
service does not initiate recurring human sign-in.
An approved native connection normally stays authorized until revoked; explicit
expiry, access removal and current policy still apply. Management sign-in may be
needed for a new request or consent, not for every ordinary runtime action.

To abandon an unfinished device request, use **Cancel device request** in the app.
For a denied, expired or cancelled request, use **Archive finished request**. The
CLI equivalent confirms the exact `setup_id` shown by `setup status --json`:

```bash
atellagent-cli setup cancel --confirm-setup <setup-id>
```

This confirms the request has ended before archiving its local keys and state.
You can then request fresh approval. An enrolled device cannot be removed this
way. **Stop waiting** only closes the current interaction; it retains the request.

## Install and start

On Linux (including WSL), run the shared supervisor in the foreground after
device approval and selecting the approved controllers:

```bash
atellagent-cli service run
```

It handles device reporting/renewal and selected controllers together. Linux
endpoints use `/tmp/atellagent-<uid>/<host>.sock`; Windows uses named pipes and
macOS uses `/private/tmp/atellagent-<uid>/<host>.sock`. Each OS installation has
its own approved identity and private state. OS service installation below is
for macOS and Windows.

After device enrollment is complete:

```bash
atellagent-cli service install
atellagent-cli service start
atellagent-cli service status --json
```

Install registers the current-user OS service but does not start it. Start leaves
an already-running service alone. No agent configuration is required for a
zero-controller device. Local running/controller status is not proof of current
server authorization or fresh discovery reporting.

After an unexpected service failure, macOS retries with a 30-second launch
throttle; Windows retries up to three times at one-minute intervals. macOS does
not impose a fixed retry count. An explicit **Stop service** remains stopped for
the current session. Restarting the service retains enrollment and approved
connections; it does not grant new permissions or require routine sign-in.

## Remove local enrollment or recover a device

Normal certificate renewal runs in the background without another human login.
The default certificate lifetime is 90 days. Automatic renewal begins 30 days
before expiry, or in the final third of a shorter certificate's lifetime.
Renewal retries appear as a warning without disabling still-valid, authorized
connections. Keep the background service connected so it can finish renewal.
If setup status reports recovery is required, signing in again does not repair
the certificate. Stop the service, then explicitly archive the selected device:

```bash
atellagent-cli service stop
atellagent-cli setup archive --confirm-device <device-id>
```

The desktop app offers the same confirmed **Archive local device** action. This
keeps private keys and state in a recovery archive; it does not delete them, edit
host hooks or revoke cloud permissions. Ask your administrator to revoke the old
device in Discovery, then enroll again using the normal approved setup flow.
New agent connections need their own approval and consent; old permissions are
not transferred. Keep the archive private. For an interrupted approval or
delivery, use `setup resume` instead of archiving.

## Request an agent connection

If your administrator assigned a connection to this device, list it and pick it up:

```sh
atellagent-cli setup native preparations --json
atellagent-cli setup native pickup <assignment-id>
```

Pickup saves the local key before submitting. Retry the same assignment if
interrupted; administrator approval and your consent are still required. Use the
assignment ID as the request ID for the consent and resume commands below.

For a supported native host, request the connection on the enrolled device:

```bash
atellagent-cli setup native request --host codex --name "Coding agent"
atellagent-cli setup native saved --json
```

The request and its private key are saved before server submission. Administrator
approval remains required. Submitting your own request records consent to
agent.control on this device until revoked. Continue the original request:

```bash
atellagent-cli setup native resume <request-id>
```

Administrator-prepared connections require separate owner review using
`atellagent-cli setup native consent <request-id>`, which displays the exact
identity and scope before owner sign-in. Resume uses the original request instead
of generating a new identity.
If submission was interrupted, use `setup native submit <request-id>`.
Saved inventory is paginated; follow `next_after` with
`setup native saved --json --after <request-id>`. An inventory error does not
mean the original request is missing.

Once the approved connection's credentials are activated, select it:

```bash
atellagent-cli setup native select <request-id>
```

The running service picks up selections and starts only the selected, verified
controller. Startup can take a moment; check `service status --json` for its
current state. Selection does not install
host hooks or claim hook readiness. Configure the supported host separately and
verify a real governed request before relying on it.

If setup reports `onboarding_upgrade_required`, update the app/client and
continue the saved request. Repeated sign-in or a replacement request will not
resolve an unsupported protocol version.

Preview the exact generated hook settings before changing host configuration:

```bash
atellagent-cli setup native hooks <request-id> --json
```

This reads the currently selected local request and existing host settings, and
prints generated commands, the private endpoint, proposed additions and a review
hash. Existing settings values are not printed. Conflicting hook definitions or
unsafe settings paths require attention rather than being overwritten.
It does not change settings, approve host trust,
install a local MCP connector or verify a governed request. The preview currently
targets the standard current-user native Mac/Windows profile; overridden
`CODEX_HOME` or `CLAUDE_CONFIG_DIR` roots are rejected rather than silently pointing
at another profile. Settings writes and host readiness are separate steps.

After reviewing the generated commands and additions, save that exact plan:

```bash
atellagent-cli setup native hooks <request-id> --json --apply-review <review_sha256>
```

If the selected integration or settings changed, preview again before applying.
Saving preserves unrelated settings; it does not approve host trust or prove
hooks are active. A failure can report files already saved; review again rather
than assuming all changes were rolled back.

Enable Atellagent hooks in Codex settings. Fully quit and reopen the agent,
start a new chat, and review host permissions if prompted. Run a tool action.
The desktop setup guide updates automatically after the platform accepts a
post-tool result for that selected integration. `service status --json` reports
`hook_setup`: `settings_required`, `waiting_for_tool_result`, `verified`, or
`unavailable`. A running controller alone does not verify hooks. Verification
confirms the hook round trip, not every tool or policy. Reinstallation is not a
normal setup step.

A server request marked `failed` cannot resume. Request a replacement connection
and obtain its approval and consent; the failed request remains in history.

Only one request is selected per supported host. To change the selected request,
first preview and apply removal of its exact generated hooks:

```bash
atellagent-cli setup native hooks <original-request-id> --json --remove
atellagent-cli setup native hooks <original-request-id> --json --remove --apply-review <review_sha256>
```

The removal hash is distinct from installation review. Modified matching hooks
require attention; unrelated settings remain. Missing hooks are a no-op, and
settings files are not deleted. Review again if settings or selection changed.
After removal succeeds, remove the original selection, then select the replacement:

```bash
atellagent-cli setup native unselect <original-request-id>
atellagent-cli setup native select <replacement-request-id>
```

Unselect is local-only and works without browser sign-in or server access. It
requires the exact retained request and cannot remove another request selected
for that host. A running supervisor stops the affected controller on its next
selection check; other controllers and device reporting remain running. A failed
stop prevents replacement startup. Saved proposals, private credentials and
server accounts remain. This is not server revocation or immediate cancellation
of already-running actions. An administrator must revoke server access
separately when required. Existing hooks fail closed once their controller stops.

## Discovery and reporting

`atellagent-cli discover` reads the current user's supported host settings.
`--json` produces a redacted inventory; `--project /path/to/project` adds one
explicitly selected project's settings. Discovery does not modify host settings,
approve MCP entries, invoke tools or scan other users.

Reporting uses the device connection, independently of agent controllers. Manage
the installation's reporting preference and display name with:

```bash
atellagent-cli discover reporting list
atellagent-cli discover reporting status --device-id <device-id>
atellagent-cli discover reporting disable --device-id <device-id>
atellagent-cli discover reporting enable --device-id <device-id>
atellagent-cli discover reporting rename --device-id <device-id> --name "Work laptop"
```

Reports contain bounded discovery facts, not raw configurations, MCP URLs,
command arguments, credentials or prompts. Opt-out does not revoke the device
or its agent connections. Organization → Discovery distinguishes current, stale,
disabled and never-reported installations; reporting interruptions can also appear
in the organization's Alerts page. A detected hook or declared MCP entry is not
proof of a working connection. Server-side removal and reporting freshness must
be checked separately from local service status.

## Stop or uninstall the device service

```bash
atellagent-cli service stop
atellagent-cli service uninstall
```

Stop ends the shared device service and its controllers. Uninstall requires the
service to be stopped and removes only its OS registration. Device credentials
and approved controller selections remain. Use exact native unselect—not service
uninstall—to remove one controller without stopping the device service.


### Run an approved connection from a separate installation

A foreground controller can open an explicitly chosen, already enrolled installation:

```sh
atellagent-cli setup native run <request-id> \
  --installation-directory /absolute/owner-private/installation \
  --hook-control-socket /absolute/owner-private/controller.sock
```

This option is available only for `setup native run`. The installation must retain
its own approved device, native request, credentials and activation receipt; an
arbitrary directory or imported configuration cannot supply authority. The same
current device, delegation and integration checks apply. The command changes no
service selection or saved host hook settings. Use a separate private socket and
stop the foreground process when finished. Omitting the option uses the ordinary
current-user installation.
