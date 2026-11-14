# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under LICENSE.md in this source tree.
"""Actual HTTP JSON/SSE, bounded resume and single execution on failure."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from atellagent_client.proxy import MCPClient, MCPToolProxy, MCPToolTarget
from atellagent_client.sdk.config_models import BridgeDeploymentConfig


@pytest.fixture
def peer():
    state = SimpleNamespace(mode="json", calls=[], effects=0, gets=[], last_id=None,
                            executing=Event(), release=Event(), native_headers=[])
    result = {"resultType":"complete", "content":[{"type":"text", "text":"safe final result"}]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def write(self, body, content_type="application/json", status=200, **headers):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            for key, value in headers.items(): self.send_header(key, value)
            self.end_headers()
            try: self.wfile.write(body); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError): pass
            self.close_connection = True
        def do_POST(self):
            message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.calls.append(message)
            state.native_headers.append((message["method"],self.headers.get("Authorization")))
            method = message["method"]
            if state.mode=="auth":
                self.write(b"{}", status=401); return
            if method=="server/discover":
                body={"resultType":"complete", "supportedVersions":["2026-07-28"],
                      "capabilities":{"tools":{}}, "cacheScope":"private", "ttlMs":0}
            elif method=="tools/list":
                body={"resultType":"complete", "cacheScope":"private", "ttlMs":0,
                      "tools":[{"name":"echo", "inputSchema":{"type":"object", "properties":{}}}]}
            elif method=="tools/call":
                state.effects += 1
                state.last_id = message["id"]
                state.executing.set()
                if state.mode=="hang": state.release.wait(5)
                body=result
                if state.mode=="large": body={**result,"structuredContent":{"padding":"x"*(1024*1024+1)}}
                reply={"jsonrpc":"2.0", "id":message["id"], "result":body}
                if state.mode in {"sse","resume","no_cursor"}:
                    notice={"jsonrpc":"2.0", "method":"notifications/progress",
                            "params":{"progressToken":"unrequested", "progress":1, "message":"synthetic withheld progress"}}
                    events=("id: cursor-one\nretry: 1\ndata: \n\n" if state.mode=="resume" else "")
                    events += "event: message\ndata: " + json.dumps(notice) + "\n\n"
                    if state.mode=="sse": events += "event: message\ndata: " + json.dumps(reply) + "\n\n"
                    self.write(events.encode(), "text/event-stream"); return
                if state.mode=="compressed":
                    self.write(json.dumps(reply).encode(), **{"Content-Encoding":"gzip"}); return
            else:
                self.write(b"", status=202); return
            self.write(json.dumps({"jsonrpc":"2.0", "id":message["id"], "result":body}).encode())
        def do_GET(self):
            state.gets.append(self.headers.get("Last-Event-ID"))
            if state.mode!="resume": self.write(b"",status=405); return
            reply={"jsonrpc":"2.0", "id":state.last_id, "result":result}
            self.write(("event: message\ndata: "+json.dumps(reply)+"\n\n").encode(), "text/event-stream")
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True); thread.start()
    state.url=f"http://127.0.0.1:{server.server_port}/mcp"
    try: yield state
    finally:
        state.release.set(); server.shutdown(); server.server_close(); thread.join(timeout=2)


def client(peer):
    return MCPClient(BridgeDeploymentConfig(target_transport="http",target_url=peer.url), timeout_seconds=5)


def request():
    return {"jsonrpc":"2.0","id":"local-correlation","method":"tools/call","params":{"name":"echo","arguments":{}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode",["json","sse","resume"])
async def test_real_http_delivers_only_the_final_result_and_never_reposts(peer,mode):
    peer.mode=mode
    target=client(peer)
    try:
        assert (await target.manifest())["tools"][0]["name"]=="echo"
        reply=await target.invoke(request(),"canonical-action")
        assert reply["result"]["content"][0]["text"]=="safe final result"
        assert "synthetic withheld progress" not in json.dumps(reply)
        assert peer.effects==1
        calls=[c for c in peer.calls if c["method"]=="tools/call"]
        assert len(calls)==1
        assert calls[0]["params"]["_meta"]["atellagent/idempotencyKey"]=="canonical-action"
        assert peer.gets==(["cursor-one"] if mode=="resume" else [])
    finally: await target.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode",["no_cursor","large","compressed"])
async def test_incomplete_or_unbounded_result_refuses_without_another_effect(peer,mode):
    peer.mode=mode
    target=client(peer)
    try:
        await target.manifest()
        with pytest.raises(Exception): await target.invoke(request(),"canonical-action")
        assert peer.effects==1
        assert len([c for c in peer.calls if c["method"]=="tools/call"])==1
    finally: await target.close()


@pytest.mark.asyncio
async def test_authentication_failure_does_not_downgrade_or_invoke(peer):
    peer.mode="auth"
    target=client(peer)
    try:
        with pytest.raises(Exception): await target.manifest()
        assert [c["method"] for c in peer.calls]==["server/discover"]
        assert peer.effects==0
    finally: await target.close()


@pytest.mark.asyncio
async def test_tool_facade_and_bridge_use_the_same_actual_sse_adapter(peer):
    peer.mode="sse"
    async with MCPToolProxy(target=MCPToolTarget(peer.url)) as proxy:
        reply=await proxy.call_tool(tool_name="echo", arguments={}, action_key="accepted-action")
        assert reply["content"][0]["text"]=="safe final result"
    assert peer.effects==1


@pytest.mark.asyncio
async def test_cancelling_http_does_not_resubmit_the_tool(peer):
    peer.mode="hang"
    target=client(peer)
    try:
        await target.manifest()
        task=asyncio.create_task(target.invoke(request(),"accepted-action"))
        async with asyncio.timeout(2):
            while not peer.executing.is_set(): await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert peer.effects==1
        assert len([c for c in peer.calls if c["method"]=="tools/call"])==1
    finally:
        peer.release.set()
        await target.close()


@pytest.mark.asyncio
async def test_native_credentials_remain_request_scoped_through_shared_sdk_transport(peer):
    from atellagent_client.proxy.native_credentials import bind_native_authorization
    target=client(peer)
    try:
        await target.manifest()
        async def invoke(subject):
            with bind_native_authorization("Bearer local-native-"+subject):
                return await target.invoke(request(),"action-"+subject)
        await asyncio.gather(invoke("alice"),invoke("bob"))
        tokens=[h for method,h in peer.native_headers if method=="tools/call"]
        assert sorted(tokens)==["Bearer local-native-alice","Bearer local-native-bob"]
        assert peer.effects==2
        assert not any(h for method,h in peer.native_headers if method!="tools/call")
    finally: await target.close()
