"""Native and workload startup reuse exact retained renewal reconciliation."""

from dataclasses import replace
from .retained_rotation import reconcile_retained_rotation


async def reconcile_workload_rotation(*, draft, receipt, trust, stored, device_proof_provider=None):
    from .workload_runtime_config import build_workload_runtime_config
    from .workload_runtime_metadata import fetch_workload_metadata_for_credential
    async def make_config(candidate):
        metadata = await fetch_workload_metadata_for_credential(draft=draft, trust=trust, stored=candidate,
            device_proof_provider=device_proof_provider)
        return build_workload_runtime_config(draft=draft, receipt=receipt, stored=candidate,
            metadata=metadata, gateway_origin=trust.service_account_api_origin)
    return await reconcile_retained_rotation(stored, trust=trust, tenant_id=draft.setup.tenant_id,
                                            make_config=make_config, device_proof_provider=device_proof_provider)


async def reconcile_native_rotation(*, draft, assignment, activation, trust, stored, device_client):
    from .native_runtime_binding import NativeRuntimeBinding
    from .native_runtime_config import build_native_runtime_config
    binding = NativeRuntimeBinding(device_client.device, assignment.request_id, assignment.service_account_id,
        assignment.integration_id, assignment.agent_principal_id, assignment.binding_id,
        assignment.controller_profile_id, activation.authorization_id, activation.authorization_epoch,
        trust.service_account_api_origin, stored)
    async def make_config(candidate):
        metadata = await device_client.runtime_metadata(platform_origin=draft.platform_origin,
                                                       gateway_origin=trust.service_account_api_origin)
        return build_native_runtime_config(draft=draft, assignment=assignment,
                                          binding=replace(binding, stored=candidate), metadata=metadata)
    return await reconcile_retained_rotation(stored, trust=trust, tenant_id=draft.tenant_id,
        make_config=make_config, device_proof_provider=binding.request_headers)
