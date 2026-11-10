# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Deployment-owned workload layout without device, desktop or human token state."""

import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from .errors import OnboardingClientError
from .private_paths import create_private_directory
from .setup_store import SetupStore
from .workload_credential_store import WorkloadCredentialStore
from .workload_local_input_store import WorkloadLocalInputStore
from .workload_receipt_store import WorkloadReceiptStore
from .workload_runtime import open_workload_runtime
from .workload_setup_flow import WorkloadSetupFlow
from .workload_setup_store import WorkloadSetupStore
from .workload_preparation_client import WorkloadPreparationClient


class WorkloadContext:
    def __init__(self, directory, *, device_context=None):
        self.directory = Path(directory)
        self.device_context = device_context
        try:
            create_private_directory(self.directory)
        except (OSError, ValueError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc
        self.drafts = WorkloadSetupStore(self.directory / "workload-drafts")
        self.setups = SetupStore(self.directory / "workload-setups")
        self.credentials = WorkloadCredentialStore(self.directory / "workload-credentials")
        self.receipts = WorkloadReceiptStore(self.drafts)
        self.local_inputs = WorkloadLocalInputStore(self.drafts)

    def flow(self, request_id):
        draft = self.drafts.load(request_id)
        if draft.proposal.device_id is not None:
            from .workload_device_context import require_device_association
            require_device_association(self.device_context, draft)
        elif self.device_context is not None:
            raise OnboardingClientError("onboarding_workload_setup_intent_changed")
        return WorkloadSetupFlow(draft=draft, draft_store=self.drafts,
                                 receipt_store=self.receipts, credential_store=self.credentials,
                                 device_context=self.device_context)

    def proposal(self, request_id):
        """Read retained connection intent without activating it."""
        return self.drafts.load(request_id).proposal

    def configure(self, request_id, inputs):
        """Save local launch inputs without changing approved identity/settings."""
        from .workload_local_inputs import WorkloadLocalInputs
        if not isinstance(inputs, WorkloadLocalInputs):
            raise TypeError("typed workload local inputs required")
        draft = self.drafts.load(request_id)
        checked = WorkloadLocalInputs.from_payload(inputs.payload(), proposal=draft.proposal)
        self.local_inputs.save(draft=draft, inputs=checked)

    async def prepare_request(self, management, *, proposal, inputs, tenant_id, owner_user_id):
        """Persist exact intent and local inputs before any submission."""
        from .attended_workload import prepare_request
        return await prepare_request(self, management, proposal=proposal, inputs=inputs,
                                     tenant_id=tenant_id, owner_user_id=owner_user_id)

    async def submit_request(self, management, request_id):
        """Submit retained intent; administrator approval remains separate."""
        from .attended_workload import submit_request
        return await submit_request(self, management, request_id)

    async def status(self, request_id):
        """Read approval status without claiming credentials or starting runtime."""
        from .attended_workload import status
        return await status(self, request_id)

    async def prepare_bootstrap(self, management, *, name, tenant_id, owner_user_id):
        from .attended_workload import prepare_bootstrap
        return await prepare_bootstrap(self, management, name=name,
                                       tenant_id=tenant_id, owner_user_id=owner_user_id)

    def load_bootstrap(self, session_id):
        from .attended_workload import load_bootstrap
        return load_bootstrap(self, session_id)

    async def submit_bootstrap(self, management, session_id):
        setup = self.load_bootstrap(session_id)
        return await management.submit(draft=setup, store=self.setups)

    async def preparations(self, setup, *, after=None):
        if self.load_bootstrap(setup.operation_id).encode() != setup.encode():
            raise ValueError("Exact retained workload bootstrap required")
        async with WorkloadPreparationClient(setup=setup, setup_store=self.setups) as client:
            return await client.preparations(after=after)

    async def pickup_preparation(self, setup, preparation, *, inputs=None):
        if self.load_bootstrap(setup.operation_id).encode() != setup.encode():
            raise ValueError("Exact retained workload bootstrap required")
        async with WorkloadPreparationClient(setup=setup, setup_store=self.setups) as client:
            draft, receipt = await client.pickup(preparation, store=self.drafts,
                device_context=self.device_context, local_inputs_store=self.local_inputs, inputs=inputs)
        return self.receipts.bind(draft=draft, receipt=receipt)

    async def runtime(self, request_id):
        flow = await asyncio.to_thread(self.flow, request_id)
        inputs = self.local_inputs.load(flow.draft)
        config = await open_workload_runtime(flow=flow, deployment=inputs.deployment,
            local_guardrail_manifest_path=inputs.local_guardrail_manifest_path)
        return config, inputs

    @asynccontextmanager
    async def open_runtime(self, request_id, *, handler=None, mcp_manifest=None):
        """Start an approved SDK/bridge runtime and always release owned resources.

        No browser authorization or provisioning is initiated. The caller owns
        how long this context remains open and how its handlers execute.
        """
        from atellagent_client.runtime.workload import open_participant
        from .workload_runtime_ownership import runtime_ownership
        with runtime_ownership(self.device_context, request_id):
            config, inputs = await self.runtime(request_id)
            provider = None
            device_id = None
            if self.device_context is not None:
                from .workload_device_context import device_request_provider
                provider = device_request_provider(self.device_context, self.drafts.load(request_id))
                device_id = str(self.device_context.client.device.device_id)
            async with open_participant(config, inputs, handler=handler,
                                        mcp_manifest=mcp_manifest, device_proof_provider=provider,
                                        device_id=device_id) as participant:
                yield participant

    @asynccontextmanager
    async def open_client(self, request_id):
        """Open outgoing SDK calls with the same verified placement as a bridge."""
        from atellagent_client.sdk import AtellagentClient
        config, _inputs = await self.runtime(request_id)
        provider = None
        if self.device_context is not None:
            from .workload_device_context import device_request_provider
            provider = device_request_provider(self.device_context, self.drafts.load(request_id))
        client = AtellagentClient(config, device_proof_provider=provider)
        try:
            yield client
        finally:
            await client.close_async()
