Copyright (c) 2026 Atellagent, Inc. All rights reserved. See [LICENSE.md](http://license.md/).

# Atellagent Client Base Image

`Dockerfile.base` packages `atellagent-cli` and the outbound connected-runtime
library. The image exposes no port because the participant opens no inbound
Atellagent listener.

Build the core image:

```bash
docker build -f docker/Dockerfile.base -t atellagent-client-base:latest .
```

Build an integration-specific image only when needed:

```bash
docker build -f docker/Dockerfile.base \
  --build-arg ATELLAGENT_CLIENT_EXTRAS=mcp \
  -t atellagent-client-base:mcp .
```

Available extras include `mcp`, `openai`, `google`, `anthropic`, `langgraph`,
`ollama`, `huggingface-filter`, and `postgres`.
Comma-separate multiple extras.

Run with the generated YAML and a dedicated writable credential volume:

```bash
docker run --rm -it \
  -v "$PWD/config:/config:ro" \
  -v "$PWD/certs:/certs" \
  -e ATELLAGENT_CERT_PATH=/certs/client-cert.pem \
  -e ATELLAGENT_KEY_PATH=/certs/client-key.pem \
  -e PYTHONPATH=/customer \
  -v "$PWD/customer:/customer:ro" \
  atellagent-client-base:latest \
  /config/integration.yaml --handler customer_runtime:handler --target-idempotent
```

Enroll before starting the participant:

```bash
docker run --rm -it \
  -v "$PWD/config:/config:ro" \
  -v "$PWD/certs:/certs" \
  atellagent-client-base:latest \
  /config/integration.yaml --enroll \
  --cert-path /certs/client-cert.pem \
  --key-path /certs/client-key.pem
```

Routine supervised rotation replaces those two files, so `/certs` remains
writable by the image's non-root process. You may instead use a writable
secret-volume projection with the same atomic-rename semantics. Do not bake
credentials into the image. The configuration and customer-code mounts remain
read-only.

Gateway and authentication TLS use the image operating-system trust store.
There is no client CA mount, server certificate, listener certificate, or
published container port. Keep provider credentials in environment-backed
secret mounts rather than the YAML or image.
