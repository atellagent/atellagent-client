# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Last acknowledged editable label; never identity or authorization."""

import json

from .atomic_private_file import read_private_file
from .private_record_update import replace_private_record


class DeviceLabelCache:
    def __init__(self, directory, scope):
        self.path, self.scope = directory / "device-label.json", scope

    def _identity(self):
        return {"revision": 1, "device_id": str(self.scope.device_id),
            "tenant_id": str(self.scope.tenant_id), "owner_user_id": str(self.scope.owner_user_id)}

    @staticmethod
    def validate_name(value):
        if (not isinstance(value, str) or not 1 <= len(value) <= 128 or value != value.strip()
            or any(ord(item) < 32 or ord(item) == 127 for item in value)):
            raise ValueError("plain bounded device label required")
        return value

    def load(self):
        try:
            value = json.loads(read_private_file(self.path, limit=2048))
        except FileNotFoundError:
            return None
        expected = self._identity()
        if (not isinstance(value, dict) or set(value) != set(expected) | {"device_name"}
            or type(value["revision"]) is not int or any(value[key] != item for key, item in expected.items())):
            raise ValueError("exact label scope required")
        return self.validate_name(value["device_name"])

    def save(self, name):
        self.load()
        replace_private_record(self.path, json.dumps({**self._identity(),
            "device_name": self.validate_name(name)}).encode(), limit=2048)
