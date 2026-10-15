# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""One resumable device setup step, composed from the shared secure transports."""

from dataclasses import dataclass, replace

from .device_activation import ActivatedDevice, activate_stored_device
from .device_certificate_delivery import fetch_device_certificate
from .device_certificate_stage import stage_device_certificate
from .device_trust import fetch_device_trust
from .errors import OnboardingClientError
from .setup_status import SetupStatusClient
from .reporting_state import DeviceReportingStore
from .reporting_scope import ReportingScope
from .device_resume import resume_stored_device


@dataclass(frozen=True)
class DeviceSetupStep:
    stage: str
    activated: ActivatedDevice | None = None


class DeviceSetupFlow:
    def __init__(self, *, installation, draft, receipt, reporting_enabled: bool):
        if (draft.kind != "device" or receipt.kind != "device" or receipt.id != draft.operation_id
            or receipt.device_id is None or type(reporting_enabled) is not bool):
            raise ValueError("exact device setup and reporting choice required")
        self.installation, self.draft, self.receipt = installation, draft, receipt
        self.reporting_enabled = reporting_enabled
        installation.setups.require_saved(draft, expected_origin=draft.platform_origin)

    async def advance(self) -> DeviceSetupStep:
        async with SetupStatusClient(origin=self.draft.platform_origin,
            operation_id=self.draft.operation_id, proof_key=self.draft.keys.proof_key) as client:
            progress = await client.status()
        if progress.kind != "device" or progress.device_id != self.receipt.device_id:
            raise OnboardingClientError("onboarding_setup_identity_changed")
        if progress.status == "pending":
            return DeviceSetupStep("awaiting_approval")
        if progress.status not in {"approved", "consumed"}:
            raise OnboardingClientError("onboarding_setup_" + progress.status)
        if progress.status == "consumed" and self.installation.path.exists():
            saved, assigned, fingerprint = self.installation.load()
            if (saved.encode() != self.draft.encode()
                    or (assigned.id, assigned.kind, assigned.device_id, assigned.revision) != (
                        self.receipt.id, self.receipt.kind, self.receipt.device_id, self.receipt.revision)):
                raise OnboardingClientError("device_installation_already_selected")
            trust = await fetch_device_trust(saved.platform_origin)
            activated = await resume_stored_device(draft=saved, receipt=assigned, trust=trust,
                setup_store=self.installation.setups, credential_store=self.installation.credentials,
                fingerprint=fingerprint)
            return DeviceSetupStep("device_ready", activated)
        receipt = replace(self.receipt, status=progress.status, expires_at=progress.expires_at,
            claim_expires_at=progress.claim_expires_at)
        if progress.device_issuance is None:
            if progress.status == "consumed":
                raise OnboardingClientError("onboarding_response_invalid")
            staged = await stage_device_certificate(origin=self.draft.platform_origin,
                draft=self.draft, receipt=receipt, store=self.installation.setups)
            if staged.status in {"failed", "cancelled"}:
                raise OnboardingClientError("onboarding_certificate_" + staged.status)
            return DeviceSetupStep("issuing")
        operation = progress.device_issuance
        if operation["status"] in {"failed", "cancelled"}:
            raise OnboardingClientError("onboarding_certificate_" + operation["status"])
        if operation["status"] in {"pending", "in_progress"}:
            return DeviceSetupStep("issuing")
        trust = await fetch_device_trust(self.draft.platform_origin)
        delivery = await fetch_device_certificate(origin=self.draft.platform_origin,
            draft=self.draft, receipt=receipt, store=self.installation.setups, trust=trust)
        if delivery.status in {"failed", "cancelled"}:
            raise OnboardingClientError("onboarding_certificate_" + delivery.status)
        if delivery.credential is None:
            raise OnboardingClientError("onboarding_certificate_progress_changed", retryable=True)
        stored = self.installation.credentials.save(draft=self.draft, receipt=receipt, trust=trust,
            credential=delivery.credential, setup_store=self.installation.setups)
        activated = await activate_stored_device(draft=self.draft, receipt=receipt, trust=trust,
            setup_store=self.installation.setups, credential_store=self.installation.credentials,
            fingerprint=stored.credential.certificate_sha256, reporting_enabled=self.reporting_enabled)
        reporting = DeviceReportingStore(self.installation.directory / "reporting",
            ReportingScope.from_device(activated.device))
        if not reporting.initialize(reporting_enabled=activated.reporting_enabled):
            raise OnboardingClientError("onboarding_setup_busy", retryable=True)
        self.installation.save(draft=self.draft, receipt=receipt, trust=trust,
            fingerprint=stored.credential.certificate_sha256)
        return DeviceSetupStep("device_ready", activated)
