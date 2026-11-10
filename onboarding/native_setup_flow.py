# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""One native setup step; approval, consent and host registration stay distinct."""

import asyncio
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization

from .device_assignments import DeviceAssignment
from .errors import OnboardingClientError
from .native_activation import activate_stored_native
from .native_activation_receipt import NativeActivationResult, load_native_activation_receipt
from .renewed_credential import retained_history_time
from .certificate_material import parse_certificates
from .native_claim_client import NativeClaimClient
from .service_account_csr import service_account_csr
from .service_account_trust import fetch_service_account_trust


@dataclass(frozen=True)
class NativeSetupStep:
    stage: str
    assignment: DeviceAssignment
    activation: NativeActivationResult | None = None


class NativeSetupFlow:
    def __init__(self, *, draft, draft_store, credential_store, installation, device_client):
        self.draft, self.draft_store, self.credential_store = draft, draft_store, credential_store
        self.installation, self.device_client = installation, device_client

    def _retained_device(self):
        saved = self.draft_store.require_saved(self.draft)
        setup, receipt, _ = self.installation.load()
        device = self.device_client.device
        scope = (saved.tenant_id, saved.device_id, saved.owner_user_id)
        raw_key = lambda key: key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        if (setup.kind != "device" or receipt.kind != "device"
                or (setup.tenant_id, receipt.device_id, setup.owner_user_id) != scope
                or (device.tenant_id, device.device_id, device.owner_user_id) != scope
                or setup.operation_id != saved.setup_id or receipt.id != saved.setup_id
                or setup.platform_origin != saved.platform_origin
                or raw_key(device.proof_key) != raw_key(setup.keys.proof_key)):
            raise OnboardingClientError("onboarding_native_setup_intent_changed")
        return saved, device

    async def advance(self) -> NativeSetupStep:
        saved, device = await asyncio.to_thread(self._retained_device)
        assignment = await self.device_client.assignment(saved.proposal.id)
        if assignment is None:
            raise OnboardingClientError("onboarding_native_assignment_missing")
        try:
            assignment = DeviceAssignment.from_payload(assignment.payload(), device=saved)
            if assignment.proposal != saved.proposal or assignment.onboarding_session_id != saved.setup_id:
                raise ValueError("exact retained native intent required")
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_native_setup_intent_changed") from exc
        if assignment.status in {"denied", "cancelled", "expired", "failed"}:
            raise OnboardingClientError("onboarding_native_setup_" + assignment.status)
        if assignment.status == "pending":
            return NativeSetupStep("awaiting_approval", assignment)
        if assignment.owner_consented_at is None:
            if assignment.status != "approved":
                raise OnboardingClientError("onboarding_native_assignment_invalid")
            return NativeSetupStep("awaiting_owner_consent", assignment)
        trust = await fetch_service_account_trust(saved.platform_origin)
        trust.validated(expected_origin=saved.platform_origin)
        if assignment.status in {"installed", "connected"}:
            stored = await asyncio.to_thread(self.credential_store.load_selected, draft=saved, assignment=assignment,
                trust=trust, draft_store=self.draft_store)
            leaf = parse_certificates(stored.credential.certificate_pem, maximum=1)[0]
            if retained_history_time(stored.certificate_path, leaf) is not None:
                activation = load_native_activation_receipt(draft=saved, assignment=assignment, stored=stored)
                from .runtime_rotation_recovery import reconcile_native_rotation
                await reconcile_native_rotation(draft=saved, assignment=assignment, activation=activation,
                    trust=trust, stored=stored, device_client=self.device_client)
                return NativeSetupStep(activation.runtime_status, assignment, activation)
        else:
            csr = service_account_csr(saved.tls_key, tenant_id=saved.tenant_id,
                                      service_account_id=assignment.service_account_id)
            async with NativeClaimClient(origin=trust.service_account_api_origin,
                device=device, setup_id=saved.setup_id) as client:
                if assignment.status == "approved":
                    await client.stage(request_id=saved.proposal.id,
                                       intent_sha256=assignment.intent_sha256, csr_pem=csr)
                    return NativeSetupStep("issuing", assignment)
                credential = await client.certificate(platform_origin=saved.platform_origin,
                    assignment=assignment, csr_pem=csr, trust=trust)
            if credential is None:
                return NativeSetupStep("issuing", assignment)
            stored = self.credential_store.save(draft=saved, assignment=assignment, trust=trust,
                credential=credential, draft_store=self.draft_store)
            stored = self.credential_store.select(draft=saved, assignment=assignment, trust=trust,
                fingerprint=stored.credential.certificate_sha256, draft_store=self.draft_store)
        activation = await activate_stored_native(draft=saved, assignment=assignment, trust=trust,
            draft_store=self.draft_store, credential_store=self.credential_store,
            fingerprint=stored.credential.certificate_sha256, installation=self.installation)
        return NativeSetupStep(activation.runtime_status, assignment, activation)
