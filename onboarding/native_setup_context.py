# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Owned native setup layout opened only after current device mTLS authority."""

from .device_context import DeviceContext
from .native_credential_store import NativeCredentialStore
from .native_setup_flow import NativeSetupFlow
from .native_setup_store import NativeSetupStore


class NativeSetupContext(DeviceContext):
    def __init__(self, installation, setup, client, drafts, credentials):
        super().__init__(installation, setup, client)
        self.drafts, self.credentials = drafts, credentials

    @classmethod
    async def open(cls, directory):
        context = await DeviceContext.open(directory)
        try:
            drafts = NativeSetupStore(context.installation.directory / "native-drafts")
            credentials = NativeCredentialStore(context.installation.directory / "native-credentials")
            return cls(context.installation, context.setup, context.client, drafts, credentials)
        except BaseException:
            await context.client.aclose()
            raise

    def load(self, request_id):
        return self.drafts.load(request_id, device=self.client.device,
            setup_id=self.setup.operation_id, expected_origin=self.setup.platform_origin)

    def flow(self, draft):
        return NativeSetupFlow(draft=draft, draft_store=self.drafts, credential_store=self.credentials,
                               installation=self.installation, device_client=self.client)

    def prepare_request(self, *, display_name, native_host, packaging="sdk",
                        enforcement_mode="enforce", coverage_posture="strict"):
        from uuid import uuid4
        from .native_setup_draft import NativeSetupDraft
        draft = NativeSetupDraft.create(device=self.client.device, setup_id=self.setup.operation_id,
            request_id=uuid4(), platform_origin=self.setup.platform_origin, display_name=display_name,
            packaging=packaging, native_host=native_host, enforcement_mode=enforcement_mode,
            coverage_posture=coverage_posture)
        self.drafts.save_initial(draft)
        return draft

    async def submit_request(self, management, request_id):
        draft = self.load(request_id)
        return await management.submit_native(draft=draft, store=self.drafts, installation=self.installation)

    async def preparations(self, *, after=None, assignment_id=None):
        return await self.client.preparations(after=after, assignment_id=assignment_id)

    async def pickup_preparation(self, assignment_id):
        from .native_preparation_pickup import pickup_preparation
        return await pickup_preparation(self, assignment_id)

    async def review(self, request_id):
        from .native_connection_review import connection_review
        return await connection_review(self, request_id)

    async def consent(self, management, request_id, *, reviewed_intent_sha256):
        assignment, _ = await self.review(request_id)
        return await management.consent_native(draft=self.load(request_id), store=self.drafts,
            assignment=assignment, reviewed_intent_sha256=reviewed_intent_sha256)

    async def runtime(self, request_id):
        from .native_runtime_config import open_native_runtime
        return await open_native_runtime(self, request_id)

    async def select(self, request_id):
        from .native_runtime_selection import NativeRuntimeSelections
        setup = await self.runtime(request_id)
        NativeRuntimeSelections(self.installation.directory / "native-runtime-selections").select(
            draft=self.load(request_id), binding=setup.binding)
        return setup

    @staticmethod
    def stop_selected(directory, request_id):
        """Offline local deselection; saved identity and cloud grants are unchanged."""
        from .native_local_draft import load_native_local_draft
        from .native_runtime_selection import NativeRuntimeSelections
        draft = load_native_local_draft(directory, request_id)
        NativeRuntimeSelections(directory / "native-runtime-selections").remove(draft=draft)
