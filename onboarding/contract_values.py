# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Strict scalar parsing for server-delivered onboarding identities/deadlines."""

from datetime import datetime
from uuid import UUID


def canonical_id(value) -> UUID:
    if not isinstance(value, str):
        raise ValueError("canonical identity required")
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError("canonical identity required")
    return parsed


def aware_time(value) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("aware server timestamp required")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("aware server timestamp required")
    return parsed
