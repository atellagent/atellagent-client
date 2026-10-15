# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Closed own-device metadata, distinct from management-login or agent readiness."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .errors import OnboardingClientError


def _time(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("aware server timestamp required")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("aware server timestamp required")
    return parsed


@dataclass(frozen=True)
class DeviceStatus:
    device_id: UUID
    tenant_id: UUID
    tenant_name: str
    owner_user_id: UUID
    device_name: str | None
    status: str
    discovery_reporting_status: str
    certificate_expires_at: datetime
    discovery_observed_at: datetime | None
    discovery_fresh: bool
    server_time: datetime

    @classmethod
    def from_payload(cls, payload: dict) -> "DeviceStatus":
        try:
            if set(payload) != set(cls.__dataclass_fields__):
                raise ValueError("closed device status required")
            if payload["status"] != "active" or payload["discovery_reporting_status"] not in ("enabled", "disabled", "never_reported"):
                raise ValueError("unknown device status")
            if type(payload["discovery_fresh"]) is not bool:
                raise ValueError("explicit freshness required")
            name = payload["device_name"]
            tenant_name = payload["tenant_name"]
            if (not isinstance(tenant_name, str) or not 1 <= len(tenant_name) <= 256
                    or any(ord(item) < 32 or ord(item) == 127 for item in tenant_name)):
                raise ValueError("plain bounded organization name required")
            if name is not None and (not isinstance(name, str) or not 1 <= len(name) <= 128
                or name != name.strip() or any(ord(item) < 32 or ord(item) == 127 for item in name)):
                raise ValueError("plain bounded name required")
            values = dict(payload)
            for field in ("device_id", "tenant_id", "owner_user_id"):
                values[field] = UUID(payload[field])
                if str(values[field]) != payload[field]:
                    raise ValueError("canonical device identity required")
            for field in ("certificate_expires_at", "server_time"):
                values[field] = _time(payload[field])
            values["discovery_observed_at"] = None if payload["discovery_observed_at"] is None else _time(payload["discovery_observed_at"])
            if values["certificate_expires_at"] <= values["server_time"]:
                raise ValueError("current device credential required")
            return cls(**values)
        except (ValueError, TypeError, AttributeError) as exc:
            raise OnboardingClientError("device_response_invalid") from exc
