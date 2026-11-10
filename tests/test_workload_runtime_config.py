"""Managed workload settings preserve all profiles and real local deployment inputs."""

from dataclasses import replace
from uuid import uuid4
import pytest
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.runtime_metadata import RuntimeMetadata
from atellagent_client.onboarding.workload_request_receipt import WorkloadRequestReceipt
from atellagent_client.onboarding.workload_runtime_config import build_workload_runtime_config
from atellagent_client.onboarding.workload_runtime import open_workload_runtime
from test_workload_credential_store import stored_case
from test_workload_proof_client import case
from test_workload_management import draft, receipt as receipt_payload
from test_workload_setup_flow import flow_case


PROFILES = [
    ("agent", ("agent.execute",), {"identity_mode": "boundary_identity_only"}),
    ("mcp_server", ("tool.execute",), {"identity_mode": "federated_agent_identity",
        "control_source": "local_manifest", "local_guardrail_mode": "enforce"}),
    ("model", ("model.inference",), {}),
    ("workflow_runtime", ("workflow.execute",), {}),
    ("channel", ("channel.ingress",), {"channel_type": "slack"}),
    ("ml_filter", ("prompt_injection",), {"filter_execution_boundary": "model_boundary"}),
]


@pytest.fixture
def configuration(stored_case):
    store, args, credential = stored_case
    stored = store.save(**args, credential=credential)
    metadata = RuntimeMetadata(args["draft"].setup.platform_origin, args["trust"].service_account_api_origin,
        "https://identity.example:9443/auth/realms/test",
        "https://identity.example:9443/auth/realms/test/protocol/openid-connect/token",
        "https://identity.example:9443/auth/realms/test/protocol/openid-connect/certs")
    return dict(draft=args["draft"], receipt=replace(args["receipt"], status="installed"),
                stored=stored, metadata=metadata, gateway_origin=metadata.gateway_origin)


@pytest.mark.parametrize("account_type,capabilities,options", PROFILES)
@pytest.mark.parametrize("packaging", ["sdk", "bridge"])
def test_all_workload_profiles_preserve_reviewed_settings(configuration, account_type, capabilities, options, packaging, tmp_path):
    args = configuration
    proposal = replace(args["draft"].proposal, account_type=account_type, capabilities=capabilities,
                       packaging=packaging, **options)
    args["draft"] = replace(args["draft"], proposal=proposal)
    old = args["receipt"]
    args["receipt"] = WorkloadRequestReceipt.from_payload({**receipt_payload(args["draft"],
        account_id=old.service_account_id, integration_id=old.integration_id), "status": "installed"}, draft=args["draft"])
    local = {} if packaging == "sdk" else {"deployment": {"type": "bridge", "target_transport": "stdio",
        "target_command": "customer-handler", "target_args": ["--serve"], "target_env_map": {"API_KEY": "CUSTOMER_API_KEY"}}}
    if account_type == "mcp_server": local["local_guardrail_manifest_path"] = str(tmp_path / "guardrail.json")
    result = build_workload_runtime_config(**args, **local)
    assert result.integration_type == ("mcp" if account_type == "mcp_server" else account_type)
    assert result.capabilities == list(capabilities) and result.packaging == packaging
    assert result.client_id == f"service-account-{old.service_account_id}"
    assert result.cert_path == str(args["stored"].certificate_path)
    assert result.oauth_token_url == args["metadata"].token_url
    assert result.identity_mode == proposal.identity_mode
    assert result.local_guardrail_mode == proposal.local_guardrail_mode
    assert result.filter_execution_boundary == proposal.filter_execution_boundary
    assert result.channel_type == proposal.channel_type
    assert not hasattr(result, "local_host_user_authorization") and result.native_host is None
    assert result.mcp_descriptor_path_template == (
        "/v1/connected-runtimes/instances/{instance_id}/descriptors/mcp" if account_type == "mcp_server" else None)
    if packaging == "bridge":
        assert result.deployment.target_command == "customer-handler"
        assert result.deployment.target_env_map == {"API_KEY": "CUSTOMER_API_KEY"}


@pytest.mark.parametrize("change", ["pending", "account", "integration", "tenant", "metadata", "profile", "deployment", "manifest"])
def test_identity_endpoint_or_authority_overrides_rejected(configuration, change):
    args = configuration
    if change == "pending": args["receipt"] = replace(args["receipt"], status="pending")
    elif change in {"account", "integration"}:
        name = {"account": "service_account_id", "integration": "integration_id"}[change]
        args["stored"] = replace(args["stored"], credential=replace(args["stored"].credential, **{name: uuid4()}))
    elif change == "tenant":
        args["draft"] = replace(args["draft"], setup=replace(args["draft"].setup, tenant_id=uuid4()))
        old = args["receipt"]
        args["receipt"] = WorkloadRequestReceipt.from_payload({**receipt_payload(args["draft"],
            account_id=old.service_account_id, integration_id=old.integration_id), "status": "installed"}, draft=args["draft"])
    elif change == "metadata": args["metadata"] = replace(args["metadata"], gateway_origin="https://foreign.example:8443")
    elif change == "profile": args["draft"] = replace(args["draft"], proposal=replace(args["draft"].proposal, display_name="Different"))
    elif change == "deployment": args["deployment"] = {"type": "sdk", "service_account_id": str(uuid4())}
    else: args["local_guardrail_manifest_path"] = "relative.json"
    with pytest.raises(OnboardingClientError): build_workload_runtime_config(**args)


@pytest.mark.asyncio
async def test_first_activation_opens_config_with_current_post_activation_status(flow_case):
    c = flow_case
    c.state.status = "issued"
    result = await open_workload_runtime(flow=c.flow)
    assert result.integration_id == str(c.credential.integration_id)
    assert result.integration_type == "model" and not hasattr(result, "local_host_user_authorization")
    assert c.state.status == "installed"
    assert any("/runtime-metadata/" in path for path in c.state.calls)
    c.state.calls.clear()
    assert (await open_workload_runtime(flow=c.flow)).cert_path == result.cert_path
    assert not any(path.endswith("/claim") or path.endswith("/certificate") for path in c.state.calls)


@pytest.mark.asyncio
async def test_pending_setup_is_not_a_runtime_configuration(flow_case):
    with pytest.raises(OnboardingClientError, match="runtime_not_activated"):
        await open_workload_runtime(flow=flow_case.flow)
    assert all(path.endswith("/status") for path in flow_case.state.calls)
