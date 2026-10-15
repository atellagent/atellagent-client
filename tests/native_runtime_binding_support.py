"""Verified stored-material fixture for native runtime request identities."""

from types import SimpleNamespace
from uuid import uuid4

from atellagent_client.onboarding.device_identity import AdmittedDevice
from atellagent_client.onboarding.native_credential_store import NativeCredentialStore
from atellagent_client.onboarding.native_runtime_binding import NativeRuntimeBinding
from atellagent_client.onboarding.setup_keys import SetupKeys
from native_credential_support import stored_case


def runtime_case(tmp_path):
    case = stored_case(tmp_path)
    store = NativeCredentialStore(tmp_path / "credentials")
    stored = store.save(**{name: getattr(case, name) for name in (
        "draft", "assignment", "trust", "credential", "draft_store")})
    device = AdmittedDevice(case.draft.tenant_id, case.draft.device_id, case.draft.owner_user_id,
        SetupKeys.generate().proof_key, tmp_path / "device.pem", tmp_path / "device-key.pem")
    assignment = case.assignment
    binding = NativeRuntimeBinding(device, assignment.request_id, assignment.service_account_id,
        assignment.integration_id, assignment.agent_principal_id, assignment.binding_id,
        assignment.controller_profile_id, uuid4(), 1, case.trust.service_account_api_origin, stored)
    return SimpleNamespace(case=case, store=store, binding=binding)
