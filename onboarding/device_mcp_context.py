"""Device-associated MCP setup/lifecycle below SDK, CLI, service and desktop."""

from .device_context import DeviceContext
from .errors import OnboardingClientError, MCPExistingRequestError
from .mcp_runtime_selection import MCPRuntimeSelections, local_inputs_digest
from .workload_context import WorkloadContext
from .process_lease import process_lease


class DeviceMCPContext(WorkloadContext):
    def __init__(self, device_context):
        super().__init__(device_context.installation.directory / "mcp-workloads", device_context=device_context)
        self.selections = MCPRuntimeSelections(device_context.installation.directory / "mcp-runtime-selections")

    @classmethod
    async def open(cls, directory):
        device = await DeviceContext.open(directory)
        try:
            return cls(device)
        except BaseException:
            await device.client.aclose()
            raise

    def review(self, request_id):
        """Local-only launch review; never upload commands/endpoints/credentials."""
        draft = self.flow(request_id).draft
        inputs = self.local_inputs.load(draft)
        if draft.proposal.account_type != "mcp_server" or draft.proposal.packaging != "bridge":
            raise OnboardingClientError("onboarding_mcp_bridge_required")
        deployment = inputs.deployment
        if deployment["target_transport"] not in {"stdio", "http"}:
            raise OnboardingClientError("onboarding_mcp_transport_unsupported")
        return dict(request_id=str(request_id), display_name=draft.proposal.display_name,
            local_inputs_sha256=local_inputs_digest(inputs), target_transport=deployment["target_transport"],
            target_command=deployment.get("target_command"), target_args=deployment.get("target_args", []),
            target_url=deployment.get("target_url"),
            target_auth_header=(deployment.get("upstream_auth_header") or "Authorization")
                if deployment.get("upstream_auth_token_env") else None,
            credential_environment_names=sorted(set(deployment.get("target_env_map", {}).values())
                | ({deployment["upstream_auth_token_env"]} if deployment.get("upstream_auth_token_env") else set())))

    async def select(self, request_id, *, reviewed_local_inputs_sha256):
        review = self.review(request_id)
        if review["local_inputs_sha256"] != reviewed_local_inputs_sha256:
            raise OnboardingClientError("onboarding_mcp_local_review_changed")
        await self.runtime(request_id)  # Approval/activation verified without launching the target.
        draft = self.flow(request_id).draft
        inputs = self.local_inputs.load(draft)
        receipt = self.receipts.load(draft)
        self.selections.select(draft=draft, inputs=inputs, receipt=receipt,
            reviewed_local_inputs_sha256=reviewed_local_inputs_sha256)

    async def prepare_target(self, management, *, target, display_name, credential_environment_names=None,
                             http_token_environment=None, http_auth_header=None):
        """Save an explicitly selected target without probing or starting it."""
        from uuid import uuid4
        from atellagent_client.discovery.mcp_connection import target_inputs
        from .workload_proposal import WorkloadProposal
        from .workload_local_inputs import WorkloadLocalInputs
        from .workload_inventory import identifiers
        device = self.device_context.client.device
        proposal = WorkloadProposal(uuid4(), display_name, "mcp_server", "bridge", ("mcp.tools_call",),
            identity_mode="boundary_identity_only")
        inputs = target_inputs(target, credential_environment_names=credential_environment_names,
            http_token_environment=http_token_environment, http_auth_header=http_auth_header)
        inputs = WorkloadLocalInputs.from_payload(inputs.payload(), proposal=proposal)
        # The duplicate scan and immutable draft/local-input commit are one reservation.
        # One stable lock file bounds lock state; contention never waits on user/UI threads.
        with process_lease(self.directory / ".mcp-request.lock") as held:
            if not held:
                raise OnboardingClientError("onboarding_mcp_request_busy", retryable=True)
            cursor = None
            while True:
                ids, cursor = identifiers(self.drafts, after=cursor)
                for request_id in ids:
                    draft = self.flow(request_id).draft
                    if local_inputs_digest(self.local_inputs.load(draft)) == local_inputs_digest(inputs):
                        try:
                            status = (await self.status(request_id)).status
                        except OnboardingClientError:
                            status = None
                        if status not in {"denied", "cancelled", "expired", "failed"}:
                            raise MCPExistingRequestError(request_id)
                if cursor is None:
                    break
            return await self.prepare_request(management,
                proposal=proposal,
                inputs=inputs,
                tenant_id=device.tenant_id, owner_user_id=device.owner_user_id)


    async def prepare_manual_target(self, management, *, display_name, url=None,
        command=None, arguments=(), credential_environment_names=None,
        http_token_environment=None, http_auth_header=None):
        """Request a local HTTP/stdio server without a host configuration entry."""
        from .mcp_manual_target import manual_target
        target = manual_target(url=url, command=command, arguments=arguments,
            credential_environment_names=credential_environment_names)
        return await self.prepare_target(management, target=target, display_name=display_name,
            credential_environment_names=credential_environment_names,
            http_token_environment=http_token_environment, http_auth_header=http_auth_header)

    async def saved(self, *, after=None):
        from .workload_inventory import mcp_page
        return await mcp_page(self, after=after)

    def bootstrap_setups(self, *, after=None):
        """List exact retained key reservations; listing confers no approval."""
        from .mcp_bootstrap_inventory import bootstrap_page
        return bootstrap_page(self, after=after)

    def stop(self, request_id):
        """Stop local selection; retain keys/history and do not revoke server identity."""
        self.selections.remove(request_id)

    @staticmethod
    def stop_selected(directory, request_id):
        """Offline local stop; it never requires a management or device login."""
        MCPRuntimeSelections(directory / "mcp-runtime-selections").remove(request_id)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.device_context.client.aclose()
