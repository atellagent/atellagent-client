# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Private setup key encoding only for owner-protected local persistence."""

from cryptography.hazmat.primitives import serialization
from atellagent_client.sdk.private_keys import load_private_key

from .setup_keys import SetupKeys


def encode_keys(keys: SetupKeys) -> dict[str, str]:
    return {name: key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode("ascii")
        for name, key in (("proof_key_pem", keys.proof_key), ("tls_key_pem", keys.tls_key))}


def decode_keys(payload: dict) -> SetupKeys:
    values = []
    for name in ("proof_key_pem", "tls_key_pem"):
        raw = payload[name]
        if not isinstance(raw, str) or not 1 <= len(raw) <= 8192:
            raise ValueError("bounded private key required")
        values.append(load_private_key(raw.encode("ascii")))
    return SetupKeys(*values)
