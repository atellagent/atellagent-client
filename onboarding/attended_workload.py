"""Shared workload setup sequencing with caller-owned authentication and UI."""

from uuid import uuid4
from dataclasses import replace

from atellagent_client._version import CLIENT_LIBRARY_VERSION
from .atomic_private_file import read_private_file
from .bootstrap_origin import bootstrap_origin
from .errors import OnboardingClientError
from .setup_draft import SetupDraft
from .setup_keys import SetupKeys, _plain_text
from .workload_local_inputs import WorkloadLocalInputs
from .workload_proposal import WorkloadProposal
from .workload_setup_draft import WorkloadSetupDraft
from .workload_submission import submit_saved_workload


async def prepare_request(context, management, *, proposal, inputs, tenant_id, owner_user_id):
    if not isinstance(proposal, WorkloadProposal) or not isinstance(inputs, WorkloadLocalInputs):
        raise TypeError("typed workload proposal and local inputs required")
    checked_inputs = WorkloadLocalInputs.from_payload(inputs.payload(), proposal=proposal)
    identity = await management.identity()
    identity.require_selection(expected_user_id=owner_user_id, tenant_id=tenant_id)
    if context.device_context is not None:
        device = context.device_context.client.device
        if proposal.device_id is not None or proposal.device_onboarding_session_id is not None:
            raise ValueError("Device association is supplied by the verified context, not caller IDs")
        proposal = replace(proposal, device_id=device.device_id,
            device_onboarding_session_id=context.device_context.setup.operation_id)
    elif proposal.device_id is not None:
        raise OnboardingClientError("onboarding_mcp_device_required")
    draft = WorkloadSetupDraft(SetupDraft(uuid4(), tenant_id, owner_user_id, management.origin,
        "workload", proposal.display_name, CLIENT_LIBRARY_VERSION, None, SetupKeys.generate()), proposal)
    if context.device_context is not None:
        from .workload_device_context import require_device_association
        require_device_association(context.device_context, draft)
    context.drafts.save_prepared(draft=draft, inputs=checked_inputs)
    return draft


async def submit_request(context, management, request_id):
    draft = context.drafts.load(request_id)
    await submit_saved_workload(management, context=context, draft=draft)
    return context.flow(request_id)


async def prepare_bootstrap(context, management, *, name, tenant_id, owner_user_id):
    _plain_text(name, 128)
    bootstrap_origin(management.origin)
    identity = await management.identity()
    identity.require_selection(expected_user_id=owner_user_id, tenant_id=tenant_id)
    if context.device_context is not None:
        device = context.device_context.client.device
        if ((tenant_id, owner_user_id) != (device.tenant_id, device.owner_user_id)
                or management.origin != context.device_context.setup.platform_origin):
            raise OnboardingClientError("onboarding_mcp_device_changed")
    setup = SetupDraft(uuid4(), tenant_id, owner_user_id, management.origin,
        "workload", name, CLIENT_LIBRARY_VERSION, None, SetupKeys.generate())
    context.setups.save_initial(setup)
    return setup


def load_bootstrap(context, session_id):
    try:
        setup = SetupDraft.decode(read_private_file(context.setups._path(session_id), limit=32768))
        if setup.operation_id != session_id or setup.kind != "workload":
            raise ValueError("exact workload bootstrap required")
        saved = context.setups.require_saved(setup, expected_origin=setup.platform_origin)
        if context.device_context is not None:
            device = context.device_context.client.device
            if ((saved.tenant_id, saved.owner_user_id) != (device.tenant_id, device.owner_user_id)
                    or saved.platform_origin != context.device_context.setup.platform_origin):
                raise OnboardingClientError("onboarding_mcp_device_changed")
        return saved
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise OnboardingClientError("onboarding_workload_bootstrap_unavailable") from exc


async def status(context, request_id):
    from .workload_proof_client import WorkloadProofClient
    draft = context.drafts.load(request_id)
    receipt = context.receipts.load(draft)
    async with WorkloadProofClient(draft=draft, store=context.drafts, receipt=receipt) as client:
        return await client.status()
