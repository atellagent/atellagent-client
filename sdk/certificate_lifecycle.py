# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Certificate scheduling metadata never grants or extends authority."""

from datetime import datetime, timedelta


def certificate_renewal_due_at(not_before: datetime, expires_at: datetime) -> datetime:
    if (not_before.tzinfo is None or expires_at.tzinfo is None or expires_at <= not_before):
        raise ValueError("Valid timezone-aware certificate lifetime required")
    return expires_at - min(timedelta(days=30), (expires_at - not_before) / 3)
