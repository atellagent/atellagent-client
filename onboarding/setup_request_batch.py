"""One attended session submits retained independent requests without activation."""

from types import SimpleNamespace
from uuid import UUID

from .errors import OnboardingClientError
from .native_management import submit_native_proposal
from .native_setup_store import NativeSetupStore
from .setup_request_batch_store import SetupRequestBatchStore
from .setup_request_choices import SetupRequestChoices
from .workload_context import WorkloadContext
from .workload_management import submit_workload_request


class SetupRequestBatch:
    """Grouping is local presentation, not a new credential or authorization identity."""

    def __init__(self, device_setup):
        if device_setup.lease is None:
            raise OnboardingClientError("onboarding_setup_lease_required")
        self.owner = device_setup
        self.installation = device_setup.installation
        self.native = NativeSetupStore(self.installation.directory / "native-drafts")
        self.workloads = WorkloadContext(self.installation.directory / "mcp-workloads")
        self.store = SetupRequestBatchStore(self.installation.directory)

    def _identity(self):
        if self.owner.lease is None:
            raise OnboardingClientError("onboarding_setup_lease_required")
        setup, receipt, _ = self.owner.pending.load()
        if receipt is None or receipt.device_id is None:
            raise OnboardingClientError("onboarding_device_request_required")
        return setup, receipt

    def prepare(self, selections):
        choices = SetupRequestChoices.from_payload(selections)
        setup, receipt = self._identity()
        payload, choices = self.store.create(setup, receipt, choices)
        from .setup_request_batch_prepare import prepare_drafts
        prepare_drafts(self, setup, receipt, payload, choices)
        return payload

    def needs_submission(self):
        pending = self.store.needs_submission()
        if pending:
            setup, receipt = self._identity()
            self.store.load(setup, receipt)
        return pending

    async def submit(self, management):
        setup, receipt = self._identity()
        payload, choices = self.store.load(setup, receipt)
        from .setup_request_batch_prepare import prepare_drafts
        prepare_drafts(self, setup, receipt, payload, choices)
        device = SimpleNamespace(tenant_id=setup.tenant_id, owner_user_id=setup.owner_user_id,
                                 device_id=receipt.device_id)
        results = []
        for kind, identifiers in (("native", payload["native_ids"]), ("mcp", payload["mcp_ids"])):
            for value in identifiers:
                try:
                    if kind == "native":
                        draft = self.native.load(UUID(value), device=device,
                            setup_id=setup.operation_id, expected_origin=setup.platform_origin)
                        await submit_native_proposal(management, draft=draft, store=self.native,
                            device_setup=setup, device_receipt=receipt)
                    else:
                        draft = self.workloads.drafts.load(UUID(value))
                        await management.submit(draft=draft.setup, store=self.workloads.setups)
                        assigned = await submit_workload_request(management, draft=draft, store=self.workloads.drafts,
                            device_request=(setup, receipt))
                        self.workloads.receipts.bind(draft=draft, receipt=assigned)
                    results.append(dict(kind=kind, request_id=value, status="submitted"))
                except OnboardingClientError as exc:
                    results.append(dict(kind=kind, request_id=value, status="unconfirmed", code=exc.code))
        if all(item["status"] == "submitted" for item in results):
            self.store.mark_submitted()
        return results
