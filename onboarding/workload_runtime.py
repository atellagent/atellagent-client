# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Open an approved workload without desktop/device or ongoing human credentials."""

from .errors import OnboardingClientError
from .service_account_trust import fetch_service_account_trust
from .workload_runtime_config import build_workload_runtime_config
from .workload_runtime_metadata import fetch_workload_runtime_metadata
from .workload_proof_client import WorkloadProofClient
from .renewed_credential import load_renewed_credential


async def open_workload_runtime(*, flow, deployment=None, local_guardrail_manifest_path=None):
    step = await flow.advance()
    if step.activation is None or step.stage not in {"awaiting_registration", "registered"}:
        raise OnboardingClientError("onboarding_workload_runtime_not_activated")
    saved = flow.draft_store.require_saved(flow.draft)
    async with WorkloadProofClient(draft=saved, store=flow.draft_store, receipt=step.receipt) as client:
        current = await client.status()
    trust = await fetch_service_account_trust(saved.setup.platform_origin)
    verified = trust.validated(expected_origin=saved.setup.platform_origin)
    arguments = dict(draft=saved, receipt=current, trust=trust,
                     draft_store=flow.draft_store, credential_store=flow.credential_store,
                     device_proof_provider=flow.device_request_provider())
    metadata = await fetch_workload_runtime_metadata(**arguments)
    stored = flow.credential_store.load(draft=saved, receipt=current,
                                       trust=trust, draft_store=flow.draft_store)
    stored = load_renewed_credential(stored, trust=trust, tenant_id=saved.setup.tenant_id)
    return build_workload_runtime_config(draft=saved, receipt=current, stored=stored,
        metadata=metadata, gateway_origin=verified.service_account_api_origin,
        deployment=deployment, local_guardrail_manifest_path=local_guardrail_manifest_path)
