# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Versioned public API and compatibility-header helpers."""

from __future__ import annotations

import re
from typing import Dict, Optional

from atellagent_client._version import CLIENT_LIBRARY_VERSION
DEFAULT_API_VERSION = "v1"
DEFAULT_CONTRACT_VERSION = "v1"

_API_VERSION_RE = re.compile(r"^v\d+$")
_API_SUFFIX_RE = re.compile(r"(?:/(?:api/)?v\d+|/api)+$", re.IGNORECASE)


def normalize_api_version(value: Optional[str]) -> str:
    raw = str(value or DEFAULT_API_VERSION).strip().lower().strip("/")
    if raw.startswith("api/"):
        raw = raw[4:]
    if raw.isdigit():
        raw = f"v{raw}"
    if not _API_VERSION_RE.fullmatch(raw):
        raise ValueError(
            "api_version must look like 'v1', 'v2', or a bare integer like '1'"
        )
    return raw


def normalize_contract_version(value: Optional[str]) -> str:
    normalized = str(value or DEFAULT_CONTRACT_VERSION).strip()
    return normalized or DEFAULT_CONTRACT_VERSION


def strip_api_suffix(url: str) -> str:
    normalized = str(url or "").strip().rstrip("/")
    if not normalized:
        return normalized
    return _API_SUFFIX_RE.sub("", normalized).rstrip("/")


def build_versioned_route(api_version: str, path: str) -> str:
    normalized_version = normalize_api_version(api_version)
    suffix = str(path or "").strip()
    if not suffix:
        return f"/{normalized_version}"
    if not suffix.startswith("/"):
        suffix = f"/{suffix}"
    return f"/{normalized_version}{suffix}"


def build_client_compat_headers(
    *,
    api_version: str,
    contract_version: str,
    client_version: str,
) -> Dict[str, str]:
    return {
        "X-Atellagent-Api-Version": normalize_api_version(api_version),
        "X-Atellagent-Contract-Version": normalize_contract_version(contract_version),
        "X-Atellagent-Client-Version": str(client_version).strip()
        or CLIENT_LIBRARY_VERSION,
    }


__all__ = [
    "CLIENT_LIBRARY_VERSION",
    "DEFAULT_API_VERSION",
    "DEFAULT_CONTRACT_VERSION",
    "build_client_compat_headers",
    "build_versioned_route",
    "normalize_api_version",
    "normalize_contract_version",
    "strip_api_suffix",
]
