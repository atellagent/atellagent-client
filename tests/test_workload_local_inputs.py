"""Private launch inputs can change without replacing approved workload identity."""

from dataclasses import replace
import json
import os
import pytest
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.process_lease import process_lease
from atellagent_client.onboarding.workload_context import WorkloadContext
from atellagent_client.onboarding.workload_local_inputs import WorkloadLocalInputs
from atellagent_client.onboarding.workload_local_input_store import WorkloadLocalInputStore
from test_workload_setup_flow import flow_case
from test_workload_credential_store import stored_case
from test_workload_proof_client import case
from test_workload_management import draft


@pytest.fixture
def local(case):
    draft, drafts, _, _ = case
    return draft, WorkloadLocalInputStore(drafts), WorkloadLocalInputs({"type": "sdk"}, handler="customer.model:Handler")


def test_inputs_update_and_reload_without_changing_saved_identity(local):
    draft, store, inputs = local
    original = store.draft_store.require_saved(draft).encode()
    with pytest.raises(OnboardingClientError, match="local_inputs_required"): store.load(draft)
    store.save(draft=draft, inputs=inputs)
    changed = replace(inputs, handler="customer.model:NewHandler", target_idempotent=True)
    assert store.save(draft=draft, inputs=changed) == changed
    assert WorkloadLocalInputStore(store.draft_store).load(draft) == changed
    assert store.draft_store.require_saved(draft).encode() == original
    if os.name != "nt": assert store._path(draft).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("field,value", [("service_account_id", "other"), ("gateway_url", "https://other.example"),
    ("cert_path", "/other.pem"), ("handler", "not a reference"), ("target_idempotent", 1),
    ("mcp_manifest_path", "/mcp.json"), ("local_guardrail_manifest_path", "relative.json")])
def test_local_inputs_cannot_override_identity_or_ignore_profile(local, field, value):
    draft, _, inputs = local
    with pytest.raises(ValueError):
        WorkloadLocalInputs.from_payload({**inputs.payload(), field: value}, proposal=draft.proposal)


def test_local_bridge_and_guardrail_inputs_remain_customer_owned(local, tmp_path):
    draft, _, _ = local
    proposal = replace(draft.proposal, account_type="mcp_server", packaging="bridge", capabilities=("tool.execute",),
        identity_mode="boundary_identity_only", control_source="local_manifest", local_guardrail_mode="enforce")
    inputs = WorkloadLocalInputs({"type": "bridge", "target_transport": "stdio", "target_command": "customer-mcp",
        "target_args": ["--serve"], "target_env_map": {"TOKEN": "CUSTOMER_TOKEN"}},
        local_guardrail_manifest_path=str(tmp_path / "guardrail.json"), mcp_manifest_path=str(tmp_path / "manifest.json"))
    checked = WorkloadLocalInputs.from_payload(inputs.payload(), proposal=proposal)
    assert checked.deployment["target_env_map"] == {"TOKEN": "CUSTOMER_TOKEN"}
    assert checked.deployment["target_command"] == "customer-mcp"
    assert "CUSTOMER_TOKEN" not in repr(checked)


@pytest.mark.parametrize("change", ["draft", "revision", "extra", "identity"])
def test_corrupt_existing_settings_are_not_overwritten(local, change):
    draft, store, inputs = local
    store.save(draft=draft, inputs=inputs)
    path = store._path(draft)
    record = json.loads(path.read_bytes())
    if change == "draft": record["draft_sha256"] = "f" * 64
    elif change == "revision": record["revision"] = True
    elif change == "extra": record["extra"] = "unknown"
    else: record["inputs"]["deployment"]["client_id"] = "foreign"
    path.write_text(json.dumps(record))
    before = path.read_bytes()
    with pytest.raises(OnboardingClientError): store.save(draft=draft, inputs=inputs)
    assert path.read_bytes() == before


def test_busy_writer_and_interrupted_update_preserve_prior_settings(local, monkeypatch):
    draft, store, inputs = local
    store.save(draft=draft, inputs=inputs)
    lock = store.draft_store.directory / f".{draft.proposal.id}-local.lock"
    with process_lease(lock) as held:
        assert held
        with pytest.raises(OnboardingClientError, match="local_inputs_busy"):
            store.save(draft=draft, inputs=replace(inputs, handler="customer.other:Handler"))
    def unavailable(*args, **kwargs): raise OSError("interrupted write")
    monkeypatch.setattr("atellagent_client.onboarding.workload_local_input_store.replace_private_record", unavailable)
    with pytest.raises(OnboardingClientError): store.save(draft=draft, inputs=replace(inputs, handler="customer.other:Handler"))
    assert store.load(draft) == inputs


@pytest.mark.asyncio
async def test_context_reopens_real_workload_and_launch_inputs(flow_case, tmp_path):
    c = flow_case
    context = WorkloadContext(tmp_path / "deployment")
    context.drafts.save_initial(c.args["draft"])
    inputs = WorkloadLocalInputs({"type": "sdk"}, handler="customer.model:Handler", target_idempotent=True)
    context.local_inputs.save(draft=c.args["draft"], inputs=inputs)
    context.receipts.bind(draft=c.args["draft"], receipt=c.args["receipt"])
    context.credentials.save(**{**c.args, "draft_store": context.drafts}, credential=c.credential)
    c.state.status = "issued"
    config, launch = await context.runtime(c.args["draft"].proposal.id)
    assert config.integration_id == str(c.credential.integration_id) and launch == inputs
    reopened = WorkloadContext(context.directory)
    second, launch = await reopened.runtime(c.args["draft"].proposal.id)
    assert second.cert_path == config.cert_path and launch == inputs
    assert not any(path.endswith("/claim") or path.endswith("/certificate") for path in c.state.calls)
