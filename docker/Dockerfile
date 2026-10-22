# syntax=docker/dockerfile:1.10
# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

# `3.11.17-slim-bookworm`, pinned to its multi-platform OCI index on 2026-10-13.
FROM python:3.11.17-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89

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

# The pinned base includes Debian security updates for OpenSSL, Perl and PCRE2.
# Native components bundled by Python wheels are inventoried after installation.
COPY security/native_components.py /tmp/native_components.py
COPY security/verify_public_trust.py /tmp/verify_public_trust.py
COPY security/incident-chains/ /tmp/incident-chains/

RUN test -n "$CLIENT_VERSION" \
    && test -n "$VCS_REF" \
    && test -n "$BUILD_DATE" \
    && test -n "$CLIENT_WHEEL_SHA256" \
    && test "$(find /tmp -maxdepth 1 -type f -name 'atellagent_client-*.whl' | wc -l)" -eq 1 \
    && wheel=$(find /tmp -maxdepth 1 -type f -name 'atellagent_client-*.whl' -print -quit) \
    && test "$CLIENT_WHEEL_SHA256" = "$(sha256sum "$wheel" | awk '{print $1}')" \
    && rm -f /usr/share/ca-certificates/mozilla/emSign_ECC_Root_CA_-_G3.crt /usr/share/ca-certificates/mozilla/Comodo_AAA_Services_root.crt \
    && sed -i '\|^mozilla/emSign_ECC_Root_CA_-_G3.crt$|d' /etc/ca-certificates.conf \
    && sed -i '\|^mozilla/Comodo_AAA_Services_root.crt$|d' /etc/ca-certificates.conf \
    && update-ca-certificates --fresh \
    && python -m pip install --no-deps 'pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70' \
    && pip install --no-deps "$wheel" \
    && pip install --only-binary=:all: --require-hashes -r /tmp/container-core-py311.lock \
    && pip check \
    && python /tmp/native_components.py /licenses/native-components.spdx.json --created "$BUILD_DATE" \
    && rm /tmp/native_components.py \
    && rm -rf "$wheel" /tmp/container-core-py311.lock /root/.cache \
    && rm -rf /usr/local/bin/pip* /usr/local/bin/wheel \
      /usr/local/lib/python3.11/site-packages/pip \
      /usr/local/lib/python3.11/site-packages/pip-*.dist-info \
      /usr/local/lib/python3.11/site-packages/setuptools \
      /usr/local/lib/python3.11/site-packages/setuptools-*.dist-info \
      /usr/local/lib/python3.11/site-packages/wheel \
      /usr/local/lib/python3.11/site-packages/wheel-*.dist-info \
      /usr/local/lib/python3.11/site-packages/packaging \
      /usr/local/lib/python3.11/site-packages/packaging-*.dist-info \
      /usr/local/lib/python3.11/ensurepip

# Reject every published compromised TLS chain, including Sectigo cross-signs.
RUN python /tmp/verify_public_trust.py --chains-directory /tmp/incident-chains \
    && rm -rf /tmp/verify_public_trust.py /tmp/incident-chains

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
