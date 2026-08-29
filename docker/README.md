Copyright (c) 2026 Atellagent, Inc. All rights reserved. See [LICENSE.md](../LICENSE.md).

# Portable Atellagent Client image

`ghcr.io/atellagent/atellagent-client` is a portable customer-operated runtime
image. GHCR is the publication location, not where a customer must run it. The
image opens no inbound listener or port. It contains the public client core,
hash-locked runtime dependencies, the operating-system trust store, and license
notices—never customer configuration, certificates, provider credentials,
source checkout, tests, or optional provider SDKs.

Production deployments pin an immutable manifest digest:

```text
ghcr.io/atellagent/atellagent-client@sha256:<manifest-digest>
```

Verify it before deployment with `docker buildx imagetools inspect
ghcr.io/atellagent/atellagent-client@sha256:<manifest-digest>`. The protected
release workflow attaches provenance and an SPDX SBOM to that manifest.

## One-shot enrollment, then runtime

Keep generated configuration read-only and use a dedicated writable identity
volume. Enrollment is a separate, one-shot invocation:

```bash
docker volume create atellagent-identity
docker run --rm -it --user 10001:10001 \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  -v "$PWD/config:/config:ro" \
  -v atellagent-identity:/var/lib/atellagent/identity \
  -e ATELLAGENT_CERT_PATH=/var/lib/atellagent/identity/client-cert.pem \
  -e ATELLAGENT_KEY_PATH=/var/lib/atellagent/identity/client-key.pem \
  ghcr.io/atellagent/atellagent-client@sha256:<manifest-digest> \
  /config/integration.yaml --enroll
```

The core image deliberately has no customer handler. Run a connected participant
from a pinned derived image or a read-only customer-code mount:

```bash
docker run --rm --user 10001:10001 \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges \
  -v "$PWD/config:/config:ro" \
  -v atellagent-identity:/var/lib/atellagent/identity \
  -v "$PWD/customer:/customer:ro" -e PYTHONPATH=/customer \
  -e ATELLAGENT_CERT_PATH=/var/lib/atellagent/identity/client-cert.pem \
  -e ATELLAGENT_KEY_PATH=/var/lib/atellagent/identity/client-key.pem \
  ghcr.io/atellagent/atellagent-client@sha256:<manifest-digest> \
  /config/integration.yaml --handler customer_runtime:handler --target-idempotent
```

The identity volume is writable because normal certificate rotation replaces the
certificate and private key atomically. Never bake those files, an enrollment
token, or provider credentials into an image or build input.

## Derived images

Customer extras and adapters belong in a separate image pinned to the base
digest. The base release stays core-only.

```dockerfile
FROM ghcr.io/atellagent/atellagent-client@sha256:<manifest-digest>
USER root
COPY customer-requirements.lock /tmp/customer-requirements.lock
RUN pip install --require-hashes --only-binary=:all: -r /tmp/customer-requirements.lock \
    && rm -rf /tmp/customer-requirements.lock /root/.cache
COPY --chown=10001:10001 customer_runtime.py /customer/customer_runtime.py
USER 10001:10001
```

Build and scan a derived image in the customer's release process. Its
configuration and identity mounts stay separate from the image.

## Compose and Kubernetes hardening

```yaml
services:
  participant:
    image: ghcr.io/atellagent/atellagent-client@sha256:<manifest-digest>
    user: "10001:10001"
    read_only: true
    cap_drop: ["ALL"]
    security_opt: ["no-new-privileges:true"]
    tmpfs: ["/tmp:rw,noexec,nosuid,size=16m"]
    volumes:
      - ./config:/config:ro
      - atellagent-identity:/var/lib/atellagent/identity
      - ./customer:/customer:ro
    environment:
      PYTHONPATH: /customer
      ATELLAGENT_CERT_PATH: /var/lib/atellagent/identity/client-cert.pem
      ATELLAGENT_KEY_PATH: /var/lib/atellagent/identity/client-key.pem
    command: [/config/integration.yaml, --handler, customer_runtime:handler, --target-idempotent]
volumes:
  atellagent-identity: {}
```

For Kubernetes, use `runAsUser`, `runAsGroup`, and `fsGroup` `10001`, a
read-only root filesystem, dropped capabilities, no privilege escalation, a
writable `emptyDir` for `/tmp`, and a dedicated writable identity volume. Mount
configuration and customer code read-only. This participant needs no Service
unless a customer adapter separately adds and secures an inbound surface.

## Updates, rollback, hooks, and release administration

Promote only a verified digest; roll back by restoring the prior digest, never
by rebuilding or retagging an exact version. Mutable aliases such as `latest`
are not production deployment identifiers.

The owner-only external-host hook socket is best run natively on a desktop.
Docker Desktop does not preserve the same user-owned Unix-socket boundary
between macOS/Windows and Linux containers. A Linux container deployment needs
a deliberately private runtime socket mount. With the image's default runtime
identity, use `--tmpfs /run/atellagent:rw,noexec,nosuid,size=1m,uid=10001,gid=10001,mode=700`
in addition to the `/tmp` mount; a default root-owned tmpfs is intentionally
rejected by hook control.

Before the first publish, protect the `client-release` GitHub environment and
grant the publisher only that environment's workflow token. The first approved
release creates the GHCR package; its OCI source, license, and description
labels link it to this repository. Immediately after that release, verify the
package linkage and set its public visibility and package-page metadata. The
workflow publishes one `linux/amd64` and `linux/arm64` manifest, refuses an
existing exact version, and attaches provenance/SBOM material. It does not
mirror to Docker Hub, and no image is published by a merge or test run.
