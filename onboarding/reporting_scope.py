# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Local preference scope is identity metadata, not network admission."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ReportingScope:
    tenant_id: UUID
    device_id: UUID
    owner_user_id: UUID

    def __post_init__(self):
        if not all(isinstance(item, UUID) for item in (self.tenant_id, self.device_id, self.owner_user_id)):
            raise ValueError("exact reporting scope required")

    @classmethod
    def from_device(cls, device):
        return cls(device.tenant_id, device.device_id, device.owner_user_id)
