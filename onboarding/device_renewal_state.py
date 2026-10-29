# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Local renewal hint, not evidence of current device or execution authority."""

import json
import re

from .atomic_private_file import read_private_file
from .private_record_update import replace_private_record
from .process_lease import process_lease

RENEWAL_REASONS = (None, "device_renewal_retrying", "device_renewal_recovery_required")


class DeviceRenewalState:
    def __init__(self, directory, scope):
        self.scope = scope
        self.path = directory / "device-renewal-status.json"
        self.lock = directory / "device-renewal-status.lock"

    def _identity(self):
        return dict(revision=1, tenant_id=str(self.scope.tenant_id),
            device_id=str(self.scope.device_id), owner_user_id=str(self.scope.owner_user_id))

    def read(self):
        try:
            value = json.loads(read_private_file(self.path, limit=1024))
        except FileNotFoundError:
            return None
        expected = self._identity()
        if (not isinstance(value, dict) or set(value) != set(expected) | {"reason_code", "certificate_sha256"}
                or type(value["revision"]) is not int
                or any(value[key] != item for key, item in expected.items())
                or value["reason_code"] not in RENEWAL_REASONS
                or not isinstance(value["certificate_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", value["certificate_sha256"])):
            raise ValueError("exact device renewal status required")
        return None if value["reason_code"] is None else value

    def save(self, reason, *, certificate_sha256):
        if reason not in RENEWAL_REASONS:
            raise ValueError("closed renewal status required")
        if not isinstance(certificate_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", certificate_sha256):
            raise ValueError("exact renewal fingerprint required")
        with process_lease(self.lock) as held:
            if not held:
                raise ValueError("device renewal status is busy")
            replace_private_record(self.path, json.dumps({**self._identity(),
                "reason_code": reason, "certificate_sha256": certificate_sha256}, sort_keys=True).encode(), limit=1024)

    def clear_if_replaced(self, fingerprint):
        current = self.read()
        if current is not None and current["certificate_sha256"] != fingerprint:
            self.save(None, certificate_sha256=fingerprint)
