"""Saved workload commands retain identity; foreground runtime cleanup is paired."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from atellagent_client.cli.main import _async_main
from atellagent_client.cli import workload_participant as participants
from atellagent_client.runtime import workload as runtime
from atellagent_client.onboarding.workload_context import WorkloadContext
from atellagent_client.onboarding.workload_local_inputs import WorkloadLocalInputs
from test_workload_setup_flow import flow_case
from test_workload_credential_store import stored_case
from test_workload_proof_client import case
from test_workload_management import draft


@pytest.fixture
def cli_case(flow_case, tmp_path):
    c = flow_case
    context = WorkloadContext(tmp_path / "deployment")
    context.drafts.save_initial(c.args["draft"])
    inputs = WorkloadLocalInputs({"type": "sdk"}, handler="customer.model:Handler", target_idempotent=True)
    context.local_inputs.save(draft=c.args["draft"], inputs=inputs)
    context.receipts.bind(draft=c.args["draft"], receipt=c.args["receipt"])
    return c, context, inputs


@pytest.mark.asyncio
async def test_verify_dispatch_opens_exact_managed_runtime(cli_case, monkeypatch):
    c, context, inputs = cli_case
    c.state.status = "issued"
    run = AsyncMock()
    monkeypatch.setattr(participants, "run_participant", run)
    await _async_main(["workload", "verify", str(c.args["draft"].proposal.id), "--state-directory", str(context.directory)])
    run.assert_awaited_once()
    selected, request_id = run.await_args.args
    assert isinstance(selected, WorkloadContext) and selected.directory == context.directory
    assert request_id == c.args["draft"].proposal.id
    config, launch = await selected.runtime(request_id)
    assert config.service_account_id == str(c.credential.service_account_id)
    assert config.integration_id == str(c.credential.integration_id)
    assert launch == inputs and run.await_args.kwargs == {"verify": True}
    assert not hasattr(config, "local_host_user_authorization") and config.native_host is None


@pytest.mark.asyncio
async def test_configure_updates_local_handler_without_network_or_identity_changes(cli_case):
    c, context, _ = cli_case
    before = context.drafts.require_saved(c.args["draft"]).encode()
    await _async_main(["workload", "configure", str(c.args["draft"].proposal.id), "--state-directory", str(context.directory),
        "--handler", "customer.other:Handler", "--target-idempotent"])
    assert context.local_inputs.load(c.args["draft"]).handler == "customer.other:Handler"
    assert context.drafts.require_saved(c.args["draft"]).encode() == before
    assert c.state.calls == []


@pytest.mark.asyncio
async def test_resume_pending_is_bounded_and_retains_request(cli_case, capsys):
    c, context, _ = cli_case
    request = str(c.args["draft"].proposal.id)
    await _async_main(["workload", "resume", request, "--state-directory", str(context.directory), "--wait-seconds", "1"])
    output = capsys.readouterr().out
    assert "Awaiting administrator approval" in output and request in output
    assert all(path.endswith("/status") for path in c.state.calls)
    assert context.drafts.require_saved(c.args["draft"]).encode() == c.args["draft"].encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [["integration.yaml", "--enroll"], ["workload", "run", "not-an-id"],
    ["workload", "run", "00000000-0000-0000-0000-000000000001", "--state-directory", "relative"]])
async def test_removed_or_invalid_cli_inputs_do_not_reach_network(flow_case, arguments):
    with pytest.raises(SystemExit): await _async_main(arguments)
    assert flow_case.state.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["manifest", "start", "none"])
async def test_private_mcp_and_participant_are_closed_on_failure_or_verify(monkeypatch, failure):
    bridge = SimpleNamespace(manifest=AsyncMock(return_value={"tools": []}), invoke=Mock(), close=AsyncMock())
    if failure == "manifest": bridge.manifest.side_effect = ValueError("bad manifest")
    participant = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), instance_id="fixture")
    if failure == "start": participant.start.side_effect = ValueError("registration failed")
    monkeypatch.setattr(runtime, "LocalMCPClient", lambda _: bridge)
    monkeypatch.setattr(runtime, "ConnectedBridge", lambda *args, **kwargs: participant)
    monkeypatch.setattr(runtime, "mount_handler", Mock())
    config = SimpleNamespace(packaging="bridge", integration_type="mcp", deployment=SimpleNamespace(target_transport="stdio"))
    inputs = WorkloadLocalInputs({"type": "bridge"}, target_idempotent=True)
    if failure == "none":
        async with runtime.open_participant(config, inputs): pass
    else:
        with pytest.raises(ValueError):
            async with runtime.open_participant(config, inputs): pass
    bridge.close.assert_awaited_once()
    if failure == "manifest": participant.stop.assert_not_awaited()
    else: participant.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_unasserted_idempotency_never_starts_target(monkeypatch):
    target = Mock()
    monkeypatch.setattr(runtime, "LocalMCPClient", target)
    with pytest.raises(ValueError, match="idempotency"):
        async with runtime.open_participant(SimpleNamespace(), WorkloadLocalInputs({"type": "sdk"})): pass
    target.assert_not_called()
