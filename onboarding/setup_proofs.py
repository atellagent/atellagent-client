# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Proof-bound setup polling remains independent of a browser session."""

import base64
from hashlib import sha256
import secrets
from time import time
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .proofs import canonical_json


def sign_setup_request(*, private_key: Ed25519PrivateKey, operation_id: UUID,
    method: str, path: str, body=None) -> dict[str, str]:
    """Sign the exact canonical target; operation IDs are not credentials."""
    if not isinstance(operation_id, UUID) or not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("setup identity and proof key required")
    if (method not in {"GET", "POST", "DELETE"} or not path.startswith("/")
        or path.startswith("//") or "#" in path
        or any(character.isspace() or ord(character) < 32 for character in path)):
        raise ValueError("invalid onboarding request target")
    timestamp, nonce = str(int(time())), secrets.token_urlsafe(18)
    digest = sha256(canonical_json(body) if body is not None else b"").hexdigest()
    material = "\n".join(("atellagent-onboarding-request-v1", str(operation_id),
        method, path, digest, timestamp, nonce)).encode("utf-8")
    signature = base64.urlsafe_b64encode(private_key.sign(material)).rstrip(b"=").decode("ascii")
    return {"X-Atellagent-Onboarding-Timestamp": timestamp,
        "X-Atellagent-Onboarding-Nonce": nonce,
        "X-Atellagent-Onboarding-Signature": signature}
