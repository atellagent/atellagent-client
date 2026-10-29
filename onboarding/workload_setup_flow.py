# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Bounded resumable workload steps; only explicit submission needs user login."""

import asyncio
from dataclasses import dataclass

from .errors import OnboardingClientError
from .service_account_csr import service_account_csr
from .service_account_trust import fetch_service_account_trust
from .workload_activation import activate_stored_workload
from .workload_activation_receipt import WorkloadActivationResult, require_workload_activation_receipt
from .renewed_credential import retained_history_time
from .certificate_material import parse_certificates
from .workload_proof_client import WorkloadProofClient
from .workload_receipt_recovery import recover_workload_receipt
from .workload_request_receipt import WorkloadRequestReceipt


@dataclass(frozen=True)
class WorkloadSetupStep:
    stage: str
    receipt: WorkloadRequestReceipt
    activation: WorkloadActivationResult | None = None


class WorkloadSetupFlow:
    def __init__(self, *, draft, draft_store, receipt_store, credential_store, device_context=None):
        self.draft, self.draft_store = draft, draft_store
        self.receipt_store, self.credential_store = receipt_store, credential_store
        self.device_context = device_context
        self.device_request_provider()  # Validate association before any setup effects.

    def device_request_provider(self):
        if self.draft.proposal.device_id is not None:
            from .workload_device_context import device_request_provider
            return device_request_provider(self.device_context, self.draft)
        if self.device_context is not None:
            raise OnboardingClientError("onboarding_workload_setup_intent_changed")
        return None

    async def advance(self):
        saved = await asyncio.to_thread(self.draft_store.require_saved, self.draft)
        provider = self.device_request_provider()
        try:
            previous = self.receipt_store.load(saved)
        except OnboardingClientError as exc:
            if exc.code != "onboarding_workload_receipt_recovery_required":
                raise  # Corruption is not a missing receipt or permission to resubmit.
            previous = await recover_workload_receipt(draft=saved, draft_store=self.draft_store,
                                                      receipt_store=self.receipt_store)
        async with WorkloadProofClient(draft=saved, store=self.draft_store, receipt=previous) as client:
            current = await client.status()
            if current.status in {"denied", "cancelled", "expired", "failed"}:
                raise OnboardingClientError("onboarding_workload_setup_" + current.status)
            if current.status == "pending":
                return WorkloadSetupStep("awaiting_approval", current)
            trust = await fetch_service_account_trust(saved.setup.platform_origin)
            trust.validated(expected_origin=saved.setup.platform_origin)
            try:
                stored = await asyncio.to_thread(self.credential_store.load, draft=saved, receipt=current,
                                                 trust=trust, draft_store=self.draft_store)
                leaf = parse_certificates(stored.credential.certificate_pem, maximum=1)[0]
                if retained_history_time(stored.certificate_path, leaf) is not None:
                    require_workload_activation_receipt(draft=saved, receipt=current, stored=stored)
                    from .runtime_rotation_recovery import reconcile_workload_rotation
                    await reconcile_workload_rotation(draft=saved, receipt=current, trust=trust, stored=stored,
                        device_proof_provider=provider)
                    stage = "registered" if current.status == "connected" else "awaiting_registration"
                    return WorkloadSetupStep(stage, current, WorkloadActivationResult(stage))
            except OnboardingClientError as exc:
                if exc.code != "onboarding_workload_credential_recovery_required":
                    raise  # Never replace corrupt or changed committed credentials.
                if current.status in {"installed", "connected"}:
                    raise  # Already activated material cannot be silently re-enrolled.
                csr = service_account_csr(saved.setup.keys.tls_key, tenant_id=saved.setup.tenant_id,
                                          service_account_id=current.service_account_id)
                if current.status == "approved":
                    await client.stage(csr_pem=csr)
                    return WorkloadSetupStep("issuing", current)
                credential = await client.certificate(csr_pem=csr, trust=trust)
                if credential is None:
                    return WorkloadSetupStep("issuing", current)
                self.credential_store.save(draft=saved, receipt=current, trust=trust,
                    credential=credential, draft_store=self.draft_store)
        activation = await activate_stored_workload(draft=saved, receipt=current, trust=trust,
            draft_store=self.draft_store, credential_store=self.credential_store,
            device_proof_provider=provider)
        return WorkloadSetupStep(activation.runtime_status, current, activation)
