# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Exact device request signatures shared by SDK and native integrations."""

import base64
from hashlib import sha256
import json
import secrets
from time import time
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEVICE_ID_HEADER = "X-Atellagent-Device-Id"
DEVICE_TIMESTAMP_HEADER = "X-Atellagent-Device-Timestamp"
DEVICE_NONCE_HEADER = "X-Atellagent-Device-Nonce"
DEVICE_SIGNATURE_HEADER = "X-Atellagent-Device-Signature"


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")


def sign_device_request(*, private_key: Ed25519PrivateKey, device_id: UUID | str,
    method: str, url: str, body=None) -> dict[str, str]:
    """Each attempt gets a fresh nonce; signatures confer no admission authority."""
    device = str(UUID(str(device_id)))
    parsed = urlsplit(url)
    if not parsed.path.startswith("/") or parsed.fragment or "\n" in method or "\r" in method:
        raise ValueError("exact device request target required")
    target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    timestamp, nonce = str(int(time())), secrets.token_urlsafe(18)
    digest = sha256(canonical_json(body) if body is not None else b"").hexdigest()
    material = "\n".join(("atellagent-device-request-v1", device, method.upper(),
        target, digest, timestamp, nonce)).encode("utf-8")
    signature = base64.urlsafe_b64encode(private_key.sign(material)).rstrip(b"=").decode("ascii")
    return {DEVICE_ID_HEADER: device, DEVICE_TIMESTAMP_HEADER: timestamp,
        DEVICE_NONCE_HEADER: nonce, DEVICE_SIGNATURE_HEADER: signature}
