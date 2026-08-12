Copyright (c) 2026 Atellagent, Inc. All rights reserved. See [LICENSE.md](http://license.md/).

# Security Policy

Report a suspected vulnerability to **security@atellagent.com**. Do not include
secrets, credentials, or customer data in a public issue.

Please include the affected client version, a concise reproduction, impact, and
any mitigations you recommend. We will acknowledge reports promptly and work
with reporters on coordinated disclosure.

Research is authorized only against systems, tenants, and accounts you own or
are expressly permitted to test. Do not access another party’s data, disrupt
shared services, or use destructive or volumetric testing.

Only the latest published client release receives security fixes. The client
and managed service are versioned independently; use the documented
compatibility contract for supported combinations.

## Deployment guidance

Protect configuration and credentials as secrets. Keep customer target
credentials in the customer environment, restrict local endpoints to the
intended process or network boundary, and run untrusted extensions in an
isolated runtime.

Use the documented configuration contract exactly. Invalid or unsupported
configuration fails closed; do not rely on compatibility aliases or fallback
behavior. Keep local configuration files owner-writable only.

During credential rotation, generate replacement keys on the runtime host and
follow the documented recovery procedure if activation cannot complete.
