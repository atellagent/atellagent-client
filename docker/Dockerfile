# syntax=docker/dockerfile:1.10
# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

# `3.11.16-slim-bookworm`, pinned to its multi-platform OCI index on 2026-08-16.
FROM python:3.11.16-slim-bookworm@sha256:2e32f7d302adc1c37428355c1e646897c0c53f4fd60b6a551245fb90ee129f91

ARG CLIENT_VERSION
ARG VCS_REF
ARG BUILD_DATE
ARG CLIENT_WHEEL_SHA256

LABEL org.opencontainers.image.title="Atellagent Client" \
      org.opencontainers.image.description="Portable connected Atellagent client runtime" \
      org.opencontainers.image.source="https://github.com/atellagent/atellagent-client" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.version="${CLIENT_VERSION}" \
      org.opencontainers.image.licenses="LicenseRef-Atellagent-Proprietary" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      io.atellagent.client-wheel-sha256="${CLIENT_WHEEL_SHA256}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TMPDIR=/tmp \
    ATELLAGENT_RUNTIME_DIR=/var/lib/atellagent \
    ATELLAGENT_IDENTITY_DIR=/var/lib/atellagent/identity \
    ATELLAGENT_HOOK_SOCKET=/run/atellagent/control.sock

WORKDIR /opt/atellagent

# The build context is a positive allowlist. It contains one already-built,
# verified wheel rather than a source checkout; no customer code or credentials
# are copied into the image.
COPY dist/atellagent_client-*.whl /tmp/
COPY requirements/container-core-py311.lock /tmp/container-core-py311.lock
COPY LICENSE.md THIRD_PARTY_NOTICES.md /licenses/

RUN test -n "$CLIENT_VERSION" \
    && test -n "$VCS_REF" \
    && test -n "$BUILD_DATE" \
    && test -n "$CLIENT_WHEEL_SHA256" \
    && test "$(find /tmp -maxdepth 1 -type f -name 'atellagent_client-*.whl' | wc -l)" -eq 1 \
    && wheel=$(find /tmp -maxdepth 1 -type f -name 'atellagent_client-*.whl' -print -quit) \
    && test "$CLIENT_WHEEL_SHA256" = "$(sha256sum "$wheel" | awk '{print $1}')" \
    && pip install --no-deps "$wheel" \
    && pip install --only-binary=:all: --require-hashes -r /tmp/container-core-py311.lock \
    && pip check \
    && rm -rf "$wheel" /tmp/container-core-py311.lock /root/.cache \
    && rm -rf /usr/local/bin/pip* /usr/local/bin/wheel \
      /usr/local/lib/python3.11/site-packages/pip \
      /usr/local/lib/python3.11/site-packages/pip-*.dist-info \
      /usr/local/lib/python3.11/site-packages/setuptools \
      /usr/local/lib/python3.11/site-packages/setuptools-*.dist-info \
      /usr/local/lib/python3.11/site-packages/wheel \
      /usr/local/lib/python3.11/site-packages/wheel-*.dist-info

# The image contains no listener. Runtime state must be supplied through the
# documented writable identity/runtime mounts and a tmpfs at /tmp.
RUN groupadd --gid 10001 atellagent \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin atellagent \
    && install -d --owner=10001 --group=10001 --mode=0700 \
      /var/lib/atellagent /var/lib/atellagent/identity /run/atellagent /tmp \
    && chown -R 10001:10001 /opt/atellagent /licenses

USER 10001:10001

# Exec form forwards SIGTERM directly to the participant process. No port is
# declared; the image is a client runtime, not an inbound service.
ENTRYPOINT ["atellagent-cli"]
CMD ["--help"]
