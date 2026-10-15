# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Exact activated native identities; server admission remains authoritative."""

import asyncio
from dataclasses import dataclass, field
from hashlib import sha256
from urllib.parse import urlsplit
from uuid import UUID

from .device_identity import AdmittedDevice
from .errors import OnboardingClientError
from .native_credential_record import StoredNativeCredential
from .origins import https_origin
from .proofs import sign_device_request
from .service_account_trust import fetch_service_account_trust
from .renewed_credential import load_renewed_credential

AUTHORIZATION_ID_HEADER = "X-Atellagent-Local-Authorization-Id"


@dataclass(frozen=True)
class NativeRuntimeBinding:
    device: AdmittedDevice = field(repr=False)
    request_id: UUID
    service_account_id: UUID
    integration_id: UUID
    agent_principal_id: UUID
    binding_id: UUID
    controller_profile_id: UUID
    authorization_id: UUID
    authorization_epoch: int
    gateway_origin: str
    stored: StoredNativeCredential = field(repr=False)

    def __post_init__(self):
        ids = (self.request_id, self.service_account_id, self.integration_id, self.agent_principal_id,
               self.binding_id, self.controller_profile_id, self.authorization_id)
        if (not isinstance(self.device, AdmittedDevice) or not all(isinstance(item, UUID) for item in ids)
                or type(self.authorization_epoch) is not int or not 1 <= self.authorization_epoch <= 2**31 - 1
                or https_origin(self.gateway_origin) != self.gateway_origin
                or not isinstance(self.stored, StoredNativeCredential)
                or (self.stored.credential.service_account_id, self.stored.credential.integration_id)
                   != (self.service_account_id, self.integration_id)):
            raise ValueError("exact activated native runtime binding required")

    def request_headers(self, method, url, body=None):
        """No human token, and no local assertion of current execution permission.

        These headers identify the exact delegation and prove device-key
        possession. Each Gateway admission independently checks current state;
        this immutable snapshot must never authorize an offline action.
        """
        target = urlsplit(url)
        if (target.scheme != "https" or target.username or target.password or target.fragment
                or https_origin(f"{target.scheme}://{target.netloc}") != self.gateway_origin):
            raise OnboardingClientError("onboarding_native_request_origin_mismatch")
        return {AUTHORIZATION_ID_HEADER: str(self.authorization_id),
            **sign_device_request(private_key=self.device.proof_key, device_id=self.device.device_id,
                                  method=method, url=url, body=body)}

    def action_key(self, host_correlation):
        if (not isinstance(host_correlation, str) or not host_correlation.strip()
                or host_correlation != host_correlation.strip() or len(host_correlation) > 1024):
            raise OnboardingClientError("onboarding_native_action_correlation_invalid")
        digest = sha256(host_correlation.encode("utf-8")).hexdigest()
        return f"local:{self.authorization_id}:{self.authorization_epoch}:{digest}"


async def open_native_runtime_binding(context, request_id):
    """Recover activation through the server before selecting a runtime identity."""
    draft = await asyncio.to_thread(context.load, request_id)
    step = await context.flow(draft).advance()
    if step.activation is None or step.stage not in {"awaiting_registration", "registered"}:
        raise OnboardingClientError("onboarding_native_runtime_not_activated")
    assignment, activation = step.assignment, step.activation
    trust = await fetch_service_account_trust(draft.platform_origin)
    trust.validated(expected_origin=draft.platform_origin)
    stored = await asyncio.to_thread(context.credentials.load_selected, draft=draft, assignment=assignment,
                                     trust=trust, draft_store=context.drafts)
    stored = await asyncio.to_thread(load_renewed_credential, stored, trust=trust, tenant_id=draft.tenant_id)
    return NativeRuntimeBinding(context.client.device, assignment.request_id, assignment.service_account_id,
        assignment.integration_id, assignment.agent_principal_id, assignment.binding_id,
        assignment.controller_profile_id, activation.authorization_id, activation.authorization_epoch,
        trust.service_account_api_origin, stored)
