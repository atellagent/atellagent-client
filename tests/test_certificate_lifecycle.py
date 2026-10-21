# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

from datetime import datetime, timedelta, timezone

import pytest

from atellagent_client.sdk.certificate_lifecycle import certificate_renewal_due_at


@pytest.mark.parametrize("days,window", [(90, 30), (30, 10), (3, 1)])
def test_renewal_window_is_bounded_by_certificate_lifetime(days, window):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expiry = start + timedelta(days=days)
    assert certificate_renewal_due_at(start, expiry) == expiry - timedelta(days=window)


def test_invalid_lifetime_is_rejected():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        certificate_renewal_due_at(now, now)
