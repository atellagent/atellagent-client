# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bootstrap trusts a canonical public origin selected before the request."""

import re
from urllib.parse import urlsplit

_HOSTNAME = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def bootstrap_origin(value: str) -> str:
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("canonical public HTTPS platform origin required")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or not _HOSTNAME.fullmatch(parsed.hostname)
        or parsed.username is not None or parsed.password is not None or parsed.path
        or parsed.query or parsed.fragment or parsed.port not in (None, 443)
        or value != f"https://{parsed.hostname}"):
        raise ValueError("canonical public HTTPS platform origin required")
    return value


def device_api_origin(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("public device API origin required")
    parsed = urlsplit(value)
    if not parsed.hostname:
        raise ValueError("public device API origin required")
    origin = bootstrap_origin("https://" + parsed.hostname)
    if parsed.port == 0:
        raise ValueError("valid device API port required")
    if value != origin + (f":{parsed.port}" if parsed.port is not None else ""):
        raise ValueError("canonical public device API origin required")
    return value
