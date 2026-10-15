"""Exact retained draft/assignment/issued credential fixture for native persistence."""

import base64
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

from atellagent_client.onboarding.device_assignments import DeviceAssignment
from atellagent_client.onboarding.native_proposal import NativeProposal
from atellagent_client.onboarding.native_setup_draft import NativeSetupDraft
from atellagent_client.onboarding.native_setup_store import NativeSetupStore
from atellagent_client.onboarding.proofs import canonical_json
from atellagent_client.onboarding.service_account_certificate_verify import verify_service_account_delivery
from service_account_certificate_support import certificate_case


def stored_case(tmp_path):
    case = certificate_case()
    args = case.args
    proposal = NativeProposal(uuid4(), "My host", "sdk", "codex", "enforce", "strict",
                             base64.b64encode(args["approved_public_key_der"]).decode())
    draft = NativeSetupDraft(uuid4(), args["tenant_id"], uuid4(), uuid4(), args["platform_origin"], proposal, case.key)
    assignment = DeviceAssignment(proposal.id, draft.setup_id, args["service_account_id"], args["integration_id"],
        uuid4(), uuid4(), uuid4(), 1, "issued", "a" * 64, proposal, "atellagent.host-hooks.v1", "until_revoked",
        ("agent.control",), case.leaf.not_valid_after_utc, case.leaf.not_valid_after_utc, None)
    payload = assignment.payload()
    intent = {key: value for key, value in payload.items() if key not in (
        "onboarding_session_id", "status", "intent_sha256", "expires_at", "claim_expires_at", "owner_consented_at")}
    intent.update(tenant_id=str(draft.tenant_id), device_id=str(draft.device_id), owner_user_id=str(draft.owner_user_id))
    payload["intent_sha256"] = sha256(canonical_json(intent)).hexdigest()
    assignment = DeviceAssignment.from_payload(payload, device=draft)
    drafts = NativeSetupStore(tmp_path / "drafts")
    drafts.save_initial(draft)
    return SimpleNamespace(draft=draft, assignment=assignment, trust=args["trust"], draft_store=drafts,
                          credential=verify_service_account_delivery(**args))
