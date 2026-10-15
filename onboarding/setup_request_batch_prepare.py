"""Idempotently prepare local drafts using the retained grouping references."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

from atellagent_client._version import CLIENT_LIBRARY_VERSION
from .atomic_private_file import read_private_file
from .errors import OnboardingClientError
from .native_setup_draft import NativeSetupDraft
from .setup_draft import SetupDraft
from .setup_keys import SetupKeys
from .workload_proposal import WorkloadProposal
from .workload_setup_draft import WorkloadSetupDraft
from .workload_local_inputs import WorkloadLocalInputs


def exists(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def prepare_drafts(batch, setup, receipt, payload, choices):
    device = SimpleNamespace(tenant_id=setup.tenant_id, owner_user_id=setup.owner_user_id,
                             device_id=receipt.device_id)
    for choice, value in zip(choices.native, payload["native_ids"]):
        identifier = UUID(value)
        if exists(batch.native._path(identifier)):
            draft = batch.native.load(identifier, device=device, setup_id=setup.operation_id,
                                      expected_origin=setup.platform_origin)
            if draft.proposal.display_name != choice["name"] or draft.proposal.native_host != choice["host"]:
                raise OnboardingClientError("onboarding_request_batch_changed")
        else:
            draft = NativeSetupDraft.create(device=device, setup_id=setup.operation_id,
                request_id=identifier, platform_origin=setup.platform_origin, display_name=choice["name"],
                native_host=choice["host"], packaging="sdk", enforcement_mode="enforce", coverage_posture="strict")
            batch.native.save_initial(draft)
    from .mcp_manual_target import manual_target
    from atellagent_client.discovery.mcp_connection import target_inputs
    for choice, value, session in zip(choices.mcp, payload["mcp_ids"], payload["workload_setup_ids"]):
        proposal = replace(WorkloadProposal(UUID(value), choice["name"], "mcp_server", "bridge", ("mcp.tools_call",),
            identity_mode="boundary_identity_only"),
            device_id=device.device_id, device_onboarding_session_id=setup.operation_id)
        path = batch.workloads.setups._path(UUID(session))
        if exists(path):
            workload = SetupDraft.decode(read_private_file(path, limit=32768))
            if (workload.operation_id != UUID(session) or workload.kind != "workload"
                    or workload.display_name != choice["name"] or workload.platform_origin != setup.platform_origin
                    or workload.tenant_id != setup.tenant_id or workload.owner_user_id != setup.owner_user_id):
                raise OnboardingClientError("onboarding_request_batch_changed")
        else:
            workload = SetupDraft(UUID(session), setup.tenant_id, setup.owner_user_id, setup.platform_origin,
                "workload", choice["name"], CLIENT_LIBRARY_VERSION, None, SetupKeys.generate())
            batch.workloads.setups.save_initial(workload)
        draft = WorkloadSetupDraft(workload, proposal)
        try:
            batch.workloads.drafts.save_initial(draft)
        except OnboardingClientError as exc:
            if exc.code != "onboarding_workload_setup_already_saved":
                raise
            batch.workloads.drafts.require_saved(draft)
        from .setup_mcp_choice import choice_target
        inputs = target_inputs(choice_target(choice))
        inputs = WorkloadLocalInputs.from_payload(inputs.payload(), proposal=proposal)
        if exists(batch.workloads.local_inputs._path(draft)):
            if batch.workloads.local_inputs.load(draft).payload() != inputs.payload():
                raise OnboardingClientError("onboarding_request_batch_changed")
        else:
            batch.workloads.local_inputs.save(draft=draft, inputs=inputs)
