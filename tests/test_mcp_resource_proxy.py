"""Original tool contracts and native authority use the existing participant."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from atellagent_client.proxy import MCPResourceProxy, NativeMCPContext
from atellagent_client.proxy.agent import MCPAgentProxyError
from test_mcp_resource_intake import runtime
from test_mcp_http_transport import peer


def context(subject="one", key="logical-action", tools=frozenset({"read"})):
    return NativeMCPContext({"issuer":"verified-fixture","subject":subject},key,tools)


def resource():
    schema={"name":"read","description":"Original tool","inputSchema":{"type":"object"},
            "outputSchema":{"type":"object"},"annotations":{"readOnlyHint":True}}
    async def released(request,**kwargs):
        return {"jsonrpc":"2.0","id":request["id"],"result":{"resultType":"complete",
            "structuredContent":{"records":[{"id":1}]},"content":[{"type":"text","text":"ok"}]}}
    runtime=SimpleNamespace(_started=True,_mcp_publication={"descriptor_revision":1},
        _mcp_manifest={"tools":[schema]},handle_mcp_request=AsyncMock(side_effect=released))
    return runtime,MCPResourceProxy(participant=runtime,authorization_boundary="native_listener"),schema


@pytest.mark.asyncio
async def test_preserves_full_catalog_and_result_through_existing_governed_intake():
    runtime,proxy,schema=resource()
    headers={"MCP-Protocol-Version":"2026-07-28"}
    catalog=await proxy.handle({"jsonrpc":"2.0","id":1,"method":"tools/list"},context=context(),headers=headers)
    assert catalog.document["result"]["tools"]==[schema]
    result=await proxy.handle({"jsonrpc":"2.0","id":7,"method":"tools/call",
        "params":{"name":"read","arguments":{"caller":"argument-is-not-identity"}}},context=context(),headers=headers)
    args=runtime.handle_mcp_request.await_args
    assert args.kwargs=={"idempotency_key":"logical-action","caller":{"issuer":"verified-fixture","subject":"one"}}
    assert args.args[0]["params"]["arguments"]=={"caller":"argument-is-not-identity"}
    assert result.document["result"]["structuredContent"]=={"records":[{"id":1}]}


@pytest.mark.asyncio
async def test_native_tool_permissions_and_session_caller_cannot_be_substituted():
    runtime,proxy,_=resource()
    initialized=await proxy.handle({"jsonrpc":"2.0","id":1,"method":"initialize",
        "params":{"protocolVersion":"2025-11-25"}},context=context())
    headers={"Mcp-Session-Id":initialized.session_id}
    with pytest.raises(MCPAgentProxyError,match="another native caller"):
        await proxy.handle({"jsonrpc":"2.0","id":2,"method":"tools/list"},context=context("two"),headers=headers)
    result=await proxy.handle({"jsonrpc":"2.0","id":3,"method":"tools/call",
        "params":{"name":"read","arguments":{}}},context=context(tools=frozenset()),headers=headers)
    assert "error" in result.document
    runtime.handle_mcp_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_context_and_wire_authority_never_reach_a_tool():
    runtime,proxy,_=resource()
    message={"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"read","arguments":{}}}
    with pytest.raises(MCPAgentProxyError): await proxy.handle(message,context={})
    message["params"]["_meta"]={"atellagent/caller":{"subject":"forged"}}
    with pytest.raises(MCPAgentProxyError):
        await proxy.handle(message,context=context(),headers={"MCP-Protocol-Version":"2026-07-28"})
    runtime.handle_mcp_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_withheld_result_is_never_returned_by_the_facade():
    runtime,proxy,_=resource()
    runtime.handle_mcp_request.side_effect=RuntimeError("private result must not escape")
    reply=await proxy.handle({"jsonrpc":"2.0","id":3,"method":"tools/call",
        "params":{"name":"read","arguments":{}}},context=context(),headers={"MCP-Protocol-Version":"2026-07-28"})
    assert "error" in reply.document and "private result" not in str(reply.document)


@pytest.mark.asyncio
async def test_native_token_reaches_only_upstream_not_the_signed_governance_boundary(runtime,peer):
    from atellagent_client.proxy import MCPClient
    from atellagent_client.sdk.config_models import BridgeDeploymentConfig
    import json
    participant,state=runtime
    target=MCPClient(BridgeDeploymentConfig(target_transport="http",target_url=peer.url))
    try:
        await target.manifest()
        participant._mcp_manifest={"tools":[{"name":"echo","inputSchema":{"type":"object"}}]}
        participant._mcp_handler=target.invoke
        proxy=MCPResourceProxy(participant=participant,authorization_boundary="native_upstream")
        native=NativeMCPContext({"issuer":"verified-native","subject":"alice"},"logical-action",
            frozenset({"echo"}),upstream_authorization="Bearer native-only-local")
        reply=await proxy.handle({"jsonrpc":"2.0","id":7,"method":"tools/call",
            "params":{"name":"echo","arguments":{"text":"safe"}}},
            context=native,headers={"MCP-Protocol-Version":"2026-07-28"})
        assert "result" in reply.document
        assert peer.effects==1
        assert ("tools/call","Bearer native-only-local") in peer.native_headers
        assert "native-only-local" not in json.dumps(state.requests)
        assert state.receipts[0]["caller"]=={"issuer":"verified-native","subject":"alice"}
    finally: await target.close()


@pytest.mark.asyncio
async def test_required_native_upstream_authority_cannot_be_replaced_by_reporting_identity():
    runtime,_,_=resource()
    proxy=MCPResourceProxy(participant=runtime,authorization_boundary="native_upstream")
    with pytest.raises(MCPAgentProxyError,match="original upstream"):
        await proxy.handle({"jsonrpc":"2.0","id":1,"method":"tools/call",
            "params":{"name":"read","arguments":{}}},context=context(),
            headers={"MCP-Protocol-Version":"2026-07-28"})
    runtime.handle_mcp_request.assert_not_awaited()
