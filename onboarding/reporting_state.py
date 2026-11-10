# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Exact device-bound reporting preference and successful-upload checkpoint."""

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path

from .atomic_private_file import read_private_file
from .reporting_scope import ReportingScope
from .private_paths import create_private_directory
from .private_record_update import replace_private_record
from .process_lease import process_lease


@dataclass(frozen=True)
class ReportingState:
    enabled: bool
    pending_sync: bool
    uploaded_at: float = 0

    def __post_init__(self):
        if (type(self.enabled) is not bool or type(self.pending_sync) is not bool
            or type(self.uploaded_at) not in {int, float} or not math.isfinite(self.uploaded_at)
            or not 0 <= self.uploaded_at <= 253402300799):
            raise ValueError("valid reporting state required")


class DeviceReportingStore:
    def __init__(self, directory: Path, scope: ReportingScope):
        if not isinstance(scope, ReportingScope):
            raise ValueError("exact reporting scope required")
        create_private_directory(directory)
        self.directory, self.scope = directory, scope
        self.path = directory / f"{scope.device_id}.json"
        self.lease_path = directory / f"{scope.device_id}.lock"

    def lease(self):
        return process_lease(self.lease_path)

    def _identity(self):
        return {"revision": 1, "tenant_id": str(self.scope.tenant_id),
            "device_id": str(self.scope.device_id), "owner_user_id": str(self.scope.owner_user_id)}

    def load(self) -> ReportingState:
        value = json.loads(read_private_file(self.path, limit=1024))
        expected = self._identity()
        if (not isinstance(value, dict) or set(value) != set(expected) | {"enabled", "pending_sync", "uploaded_at"}
            or type(value["revision"]) is not int or any(value[key] != item for key, item in expected.items())
            or type(value["enabled"]) is not bool or type(value["pending_sync"]) is not bool
            or type(value["uploaded_at"]) not in {int, float}
            or not math.isfinite(value["uploaded_at"]) or value["uploaded_at"] < 0):
            raise ValueError("exact reporting state required")
        return ReportingState(value["enabled"], value["pending_sync"], value["uploaded_at"])

    def save(self, state: ReportingState):
        payload = {**self._identity(), "enabled": state.enabled,
            "pending_sync": state.pending_sync, "uploaded_at": state.uploaded_at}
        replace_private_record(self.path, json.dumps(payload, allow_nan=False).encode(), limit=1024)

    def initialize(self, *, reporting_enabled: bool):
        if type(reporting_enabled) is not bool:
            raise ValueError("current reporting preference required")
        with self.lease() as held:
            if not held:
                return False
            try:
                self.load()
            except FileNotFoundError:
                self.save(ReportingState(reporting_enabled, False))
            return True

    def set_enabled(self, enabled: bool):
        if type(enabled) is not bool:
            raise ValueError("boolean reporting preference required")
        with self.lease() as held:
            if not held:
                return False
            try:
                state = self.load()
            except FileNotFoundError:
                state = ReportingState(enabled, True)
            self.save(replace(state, enabled=enabled, pending_sync=True, uploaded_at=0))
            return True
