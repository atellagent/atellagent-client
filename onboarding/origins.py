# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""HTTPS origins are selected explicitly; credentials never follow redirects."""

from urllib.parse import urlsplit


def https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (value != value.strip() or parsed.scheme != "https" or not parsed.hostname
        or parsed.username is not None or parsed.password is not None or parsed.query
        or parsed.fragment or parsed.path not in ("", "/") or any(ord(item) < 33 for item in value)):
        raise ValueError("a plain HTTPS service origin is required")
    parsed.port  # Reject malformed ports before any connection.
    return f"https://{parsed.netloc}"
