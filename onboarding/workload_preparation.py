"""Frozen workload settings use their real assignment ID, not a new identity."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from .contract_values import aware_time, canonical_id
from .proofs import canonical_json
from .workload_proposal import WorkloadProposal


@dataclass(frozen=True)
class WorkloadPreparation:
    id: UUID
    proposal: WorkloadProposal
    settings_sha256: str
    status: str
    expires_at: datetime
    proposed_request_id: UUID | None

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict) or set(payload) != {
            "id", "settings", "settings_sha256", "status", "expires_at", "proposed_request_id"}:
            raise ValueError("Closed workload preparation required")
        identifier = canonical_id(payload["id"])
        settings = payload["settings"]
        if not isinstance(settings, dict) or "id" in settings:
            raise ValueError("Settings cannot choose another request identity")
        proposal = WorkloadProposal.from_payload({**settings, "id": str(identifier),
            "device_id": None, "device_onboarding_session_id": None})
        digest = sha256(canonical_json(settings)).hexdigest()
        linked = None if payload["proposed_request_id"] is None else canonical_id(payload["proposed_request_id"])
        if (payload["settings_sha256"] != digest
                or payload["status"] not in ("awaiting_proposal", "proposed", "cancelled", "expired")
                or (payload["status"] == "proposed") != (linked is not None)
                or linked is not None and linked != identifier):
            raise ValueError("Exact reviewed settings and request linkage required")
        return cls(identifier, proposal, digest, payload["status"], aware_time(payload["expires_at"]), linked)
