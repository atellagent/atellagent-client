# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bounded process-local reuse of fully validated, unencrypted private keys.

Callers still read protected files and verify current certificates/authority.
Only parsing identical key bytes is reused; paths and permission decisions are
never cached. Changed bytes always undergo cryptography's full validation.
"""

from collections import OrderedDict
from threading import RLock

from cryptography.hazmat.primitives import serialization

_LIMIT = 32
_keys = OrderedDict()
_lock = RLock()


def load_private_key(encoded: bytes):
    if not isinstance(encoded, bytes) or not 1 <= len(encoded) <= 8192:
        raise ValueError("bounded private key bytes required")
    # Coalesce concurrent reads, including worker-thread reads of the device
    # shared by multiple controllers. Failed validation never enters the cache.
    with _lock:
        key = _keys.get(encoded)
        if key is None:
            key = serialization.load_pem_private_key(encoded, password=None)
            _keys[encoded] = key
            if len(_keys) > _LIMIT:
                _keys.popitem(last=False)
        else:
            _keys.move_to_end(encoded)
        return key
