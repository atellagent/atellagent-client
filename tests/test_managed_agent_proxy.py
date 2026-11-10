"""Direct MCP proxy selects saved machine authority, not YAML credentials."""

from dataclasses import replace
from io import StringIO
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.workload_context import WorkloadContext
from atellagent_client.onboarding.workload_local_inputs import WorkloadLocalInputs
from atellagent_client.proxy.agent_tools import load_agent_tools
from atellagent_client.proxy import managed_agent as module
from test_workload_management import draft


TOOL = dict(name="lookup", description="Lookup record", input_schema={"type": "object"},
            target_binding="approved-target", target_tool_name="find")


@pytest.fixture
def proxy_case(tmp_path, draft):
    draft = replace(draft, proposal=replace(draft.proposal, account_type="agent", capabilities=("agent.process",)))
    context = WorkloadContext(tmp_path / "state")
    context.drafts.save_initial(draft)
    context.local_inputs.save(draft=draft, inputs=WorkloadLocalInputs({"type": "sdk"}))
    tools = tmp_path / "tools.yaml"
    tools.write_text(json.dumps({"tools": [TOOL]}))
    return SimpleNamespace(context=context, draft=draft, args=SimpleNamespace(
        state_directory=context.directory, request_id=draft.proposal.id, tools=tools))


@pytest.mark.asyncio
async def test_stdio_uses_managed_factory_and_closes_client(proxy_case, monkeypatch):
    client = SimpleNamespace(close_async=AsyncMock(), call_mcp_tool_result_async=AsyncMock(
        return_value={"content": [{"type": "text", "text": "found"}]}))
    factory = AsyncMock(return_value=client)
    monkeypatch.setattr(module, "create_service_account_client", factory)
    requests = [dict(jsonrpc="2.0", id=1, method="initialize", params={"protocolVersion": "2025-03-26"}),
                dict(jsonrpc="2.0", id=2, method="tools/list"),
                dict(jsonrpc="2.0", id=3, method="tools/call", params={"name": "lookup", "arguments": {"id": "42"}})]
    output = StringIO()
    assert await module.run(proxy_case.args, input_stream=StringIO("\n".join(map(json.dumps, requests))),
                            output_stream=output) == 0
    options = factory.await_args.kwargs
    assert options["flow"].draft.encode() == proxy_case.draft.encode()
    assert options["deployment"] == {"type": "sdk"} and options["timeout"] == 30
    assert options["local_guardrail_manifest_path"] is None
    response = list(map(json.loads, output.getvalue().splitlines()))
    assert len(response) == 3 and response[-1]["result"]["content"][0]["text"] == "found"
    invocation = client.call_mcp_tool_result_async.await_args
    assert invocation.args == ("approved-target", "find", {"id": "42"})
    assert invocation.kwargs["source_agent"] is None
    client.close_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_authority_does_not_serve_or_create_replacement(proxy_case, monkeypatch):
    factory = AsyncMock(side_effect=OnboardingClientError("onboarding_workload_runtime_not_activated"))
    monkeypatch.setattr(module, "create_service_account_client", factory)
    output = StringIO()
    with pytest.raises(OnboardingClientError, match="not_activated"):
        await module.run(proxy_case.args, input_stream=StringIO("{}"), output_stream=output)
    assert output.getvalue() == ""
    assert proxy_case.context.drafts.load(proxy_case.draft.proposal.id).encode() == proxy_case.draft.encode()


@pytest.mark.asyncio
async def test_stdio_failure_closes_owned_client(proxy_case, monkeypatch):
    client = SimpleNamespace(close_async=AsyncMock())
    monkeypatch.setattr(module, "create_service_account_client", AsyncMock(return_value=client))
    def broken_input():
        raise OSError("closed pipe")
        yield ""
    with pytest.raises(OSError, match="closed pipe"):
        await module.run(proxy_case.args, input_stream=broken_input(), output_stream=StringIO())
    client.close_async.assert_awaited_once()


@pytest.mark.parametrize("extra", ["client_config", "source_agent", "cert_path", "gateway_url"])
def test_local_catalog_cannot_override_identity(proxy_case, extra):
    proxy_case.args.tools.write_text(json.dumps({"tools": [TOOL], extra: "other"}))
    with pytest.raises(ValueError, match="identity"):
        load_agent_tools(proxy_case.args.tools)


def test_duplicate_tool_mapping_is_rejected(proxy_case):
    proxy_case.args.tools.write_text(json.dumps({"tools": [TOOL, {**TOOL, "target_binding": "other"}]}))
    with pytest.raises(ValueError, match="unique"):
        load_agent_tools(proxy_case.args.tools)


@pytest.mark.asyncio
@pytest.mark.parametrize("account_type,packaging", [("model", "sdk"), ("agent", "bridge")])
async def test_wrong_saved_profile_is_rejected_before_network(proxy_case, monkeypatch, account_type, packaging):
    draft = replace(proxy_case.draft, proposal=replace(proxy_case.draft.proposal,
        account_type=account_type, packaging=packaging, capabilities=("model.inference",) if account_type == "model" else ("agent.process",)))
    context = WorkloadContext(proxy_case.context.directory.parent / "wrong-profile")
    context.drafts.save_initial(draft)
    proxy_case.args.state_directory = context.directory
    factory = AsyncMock()
    monkeypatch.setattr(module, "create_service_account_client", factory)
    with pytest.raises(ValueError, match="profile"):
        await module.run(proxy_case.args, input_stream=StringIO(), output_stream=StringIO())
    factory.assert_not_awaited()
