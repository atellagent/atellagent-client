# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Current setup receipt encoding, without private credentials."""

from dataclasses import asdict
from uuid import UUID


def receipt_payload(receipt):
    return {key: (value.isoformat() if hasattr(value, "isoformat") else
        str(value) if isinstance(value, UUID) else value) for key, value in asdict(receipt).items()}
