# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Bounded wire frames shared by the launcher, adapters and local transports.

The retained Edit grammar permits two 2,000,000-byte strings. JSON can expand
one accepted UTF-8 byte into six ASCII bytes (for example, \u0001). The 32 MiB
cap covers that 24 MB worst case plus the bounded control/host envelope. This
is a transport ceiling, not an argument schema or permission grant; Gateway
still validates the complete tool input and applies its smaller field limits.
"""

MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_HOST_INPUT_BYTES = MAX_REQUEST_BYTES
MAX_RESPONSE_BYTES = 1024 * 1024
