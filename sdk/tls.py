# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Outbound service-account mTLS certificate validation helpers."""

from __future__ import annotations

import os
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse

from .config_models import ServiceAccountConfig


def _expected_gateway_identity(gateway_url: str) -> Tuple[str, Optional[str], set[str]]:
    parsed = urlparse(gateway_url or "")
    host = parsed.hostname
    expected_cn = os.getenv("ATELLAGENT_GATEWAY_CERT_CN", "").strip() or None
    expected_org = os.getenv("ATELLAGENT_GATEWAY_CERT_O", "").strip() or None
    expected_sans_env = os.getenv("ATELLAGENT_GATEWAY_CERT_SAN", "").strip()
    if expected_sans_env:
        expected_sans = {
            item.strip() for item in expected_sans_env.split(",") if item.strip()
        }
    else:
        expected_sans = {
            value
            for value in (
                host,
                "mtls.gateway.atellagent.com",
            )
            if value
        }
    return expected_cn, expected_org, expected_sans


def _expected_oauth_identity(
    oauth_url: str,
) -> Tuple[str, Optional[str], set[str]]:
    expected_cn = os.getenv("ATELLAGENT_OAUTH_CERT_CN", "").strip() or None
    expected_org = os.getenv("ATELLAGENT_OAUTH_CERT_O", "").strip() or None
    expected_sans_env = os.getenv("ATELLAGENT_OAUTH_CERT_SAN", "").strip()
    if expected_sans_env:
        expected_sans = {
            item.strip() for item in expected_sans_env.split(",") if item.strip()
        }
    else:
        parsed = urlparse(oauth_url or "")
        host = parsed.hostname
        expected_sans = {
            value
            for value in (
                host,
                "mtls.auth.atellagent.com",
            )
            if value
        }
    return expected_cn, expected_org, expected_sans


def _extract_subject_value(subject, key: str) -> Optional[str]:
    for rdns in subject or []:
        for attr_key, attr_val in rdns:
            if attr_key == key:
                return attr_val
    return None


def build_gateway_cert_validator(gateway_url: str) -> Callable[[object], bool]:
    parsed = urlparse(gateway_url or "")
    if parsed.scheme and parsed.scheme.lower() != "https":
        return lambda _ssl_object: True
    expected_cn, expected_org, expected_sans = _expected_gateway_identity(gateway_url)

    def matches(cert: dict) -> bool:
        subject = cert.get("subject", [])
        cn = _extract_subject_value(subject, "commonName")
        if expected_cn and cn != expected_cn:
            return False
        if expected_org:
            org = _extract_subject_value(subject, "organizationName")
            if org != expected_org:
                return False
        sans = {val for name, val in cert.get("subjectAltName", []) if name == "DNS"}
        return not (expected_sans and not sans & expected_sans)

    def validate(ssl_object: object) -> bool:
        if ssl_object is None:
            return False
        cert = getattr(ssl_object, "getpeercert", lambda: None)()
        if not cert:
            return False
        return matches(cert)

    return validate


def build_oauth_cert_validator(oauth_url: str) -> Callable[[object], bool]:
    parsed = urlparse(oauth_url or "")
    if parsed.scheme and parsed.scheme.lower() != "https":
        return lambda _ssl_object: True
    expected_cn, expected_org, expected_sans = _expected_oauth_identity(oauth_url)

    def matches(cert: dict) -> bool:
        subject = cert.get("subject", [])
        cn = _extract_subject_value(subject, "commonName")
        if expected_cn and cn != expected_cn:
            return False
        if expected_org:
            org = _extract_subject_value(subject, "organizationName")
            if org != expected_org:
                return False
        sans = {val for name, val in cert.get("subjectAltName", []) if name == "DNS"}
        return not (expected_sans and not sans & expected_sans)

    def validate(ssl_object: object) -> bool:
        if ssl_object is None:
            return False
        cert = getattr(ssl_object, "getpeercert", lambda: None)()
        if not cert:
            return False
        return matches(cert)

    return validate


def resolve_service_account_tls(
    config: ServiceAccountConfig,
) -> Tuple[Optional[str], Optional[str]]:
    if config.cert_path and config.key_path:
        return config.cert_path, config.key_path
    return None, None


__all__ = [
    "build_gateway_cert_validator",
    "build_oauth_cert_validator",
    "resolve_service_account_tls",
]
