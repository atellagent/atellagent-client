"""Strict desired-settings pages from the current admitted device's TLS endpoint."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from .contract_values import aware_time, canonical_id
from .errors import OnboardingClientError
from .native_settings import NativeSettings
from .proofs import canonical_json


@dataclass(frozen=True)
class DevicePreparation:
    assignment_id: UUID
    status: str
    settings: NativeSettings
    settings_sha256: str
    proposed_request_id: UUID | None
    expires_at: datetime

    def payload(self):
        return dict(assignment_id=str(self.assignment_id), status=self.status, settings=self.settings.payload(),
            settings_sha256=self.settings_sha256, proposed_request_id=None if self.proposed_request_id is None
            else str(self.proposed_request_id), expires_at=self.expires_at.isoformat())

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict) or set(payload) != set(cls.__dataclass_fields__):
            raise ValueError("Closed preparation required")
        identifier = canonical_id(payload["assignment_id"])
        proposed = None if payload["proposed_request_id"] is None else canonical_id(payload["proposed_request_id"])
        settings = NativeSettings.from_payload(payload["settings"])
        digest = sha256(canonical_json(settings.payload())).hexdigest()
        if (payload["status"] not in ("awaiting_proposal", "proposed") or payload["settings_sha256"] != digest
                or (payload["status"] == "proposed") != (proposed is not None)
                or (proposed is not None and proposed != identifier)):
            raise ValueError("Exact frozen preparation and proposal identity required")
        return cls(identifier, payload["status"], settings, digest, proposed, aware_time(payload["expires_at"]))


@dataclass(frozen=True)
class DevicePreparationPage:
    preparations: tuple[DevicePreparation, ...]
    next_after: UUID | None

    @classmethod
    def from_payload(cls, payload, *, after=None, assignment_id=None):
        try:
            if (not isinstance(payload, dict) or set(payload) != {"preparations", "next_after"}
                    or not isinstance(payload["preparations"], list) or len(payload["preparations"]) > 8):
                raise ValueError("Bounded preparation page required")
            items = tuple(DevicePreparation.from_payload(item) for item in payload["preparations"])
            ids = [item.assignment_id for item in items]
            cursor = None if payload["next_after"] is None else canonical_id(payload["next_after"])
            if (ids != sorted(set(ids)) or (after is not None and any(item <= after for item in ids))
                    or (cursor is not None and (len(items) != 8 or cursor != ids[-1]))
                    or (assignment_id is not None and (len(items) > 1 or cursor is not None
                        or any(item != assignment_id for item in ids)))):
                raise ValueError("Exact advancing preparation page required")
            return cls(items, cursor)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise OnboardingClientError("device_preparation_response_invalid") from exc
