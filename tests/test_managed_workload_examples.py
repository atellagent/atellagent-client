"""Examples select approved saved profiles before creating customer targets."""

import pytest
from atellagent_client.examples.workload import load_workload_config
from atellagent_client.onboarding.workload_context import WorkloadContext
from atellagent_client.onboarding.workload_local_inputs import WorkloadLocalInputs
from test_workload_setup_flow import flow_case
from test_workload_credential_store import stored_case
from test_workload_proof_client import case
from test_workload_management import draft


@pytest.fixture
def example_case(flow_case, tmp_path):
    c = flow_case
    context = WorkloadContext(tmp_path / "example-state")
    context.drafts.save_initial(c.args["draft"])
    context.local_inputs.save(draft=c.args["draft"], inputs=WorkloadLocalInputs({"type": "sdk"}))
    context.receipts.bind(draft=c.args["draft"], receipt=c.args["receipt"])
    c.state.status = "issued"
    return c, context


@pytest.mark.asyncio
async def test_example_helper_opens_the_same_approved_workload(example_case):
    c, context = example_case
    config = await load_workload_config(context.directory, c.args["draft"].proposal.id,
                                        integration_type="model", packaging="sdk")
    assert config.integration_id == str(c.credential.integration_id)
    assert config.service_account_id == str(c.credential.service_account_id)
    assert not hasattr(config, "local_host_user_authorization")


@pytest.mark.asyncio
@pytest.mark.parametrize("integration_type,packaging", [("agent", "sdk"), ("model", "bridge")])
async def test_wrong_example_profile_fails_before_network(example_case, integration_type, packaging):
    c, context = example_case
    with pytest.raises(ValueError, match="profile"):
        await load_workload_config(context.directory, c.args["draft"].proposal.id,
                                   integration_type=integration_type, packaging=packaging)
    assert c.state.calls == []
