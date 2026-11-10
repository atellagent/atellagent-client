# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under LICENSE.md in this source tree.
"""Loopback reference MCP server; the customer listener owns native auth.

ATELLAGENT_REFERENCE_TOKEN=<local-test-secret> python -m \
  atellagent_client.examples.mcp.resource_server --state-directory /absolute/state --request-id <approved-id>

This demonstration's bearer key stays local. In a production server use its
existing verified OAuth/JWT/session context instead of this test-key adapter.
"""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import threading

from atellagent_client.connected import mount_mcp_handler
from atellagent_client.examples.workload import load_workload_config, options
from atellagent_client.governance.actions import ActionDenied
from atellagent_client.sdk import ConnectedSDKRuntime

MANIFEST = {"tools": [{"name": "echo", "description": "Return the supplied harmless text",
    "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"], "additionalProperties": False}}]}


def caller_from_auth_context(context):
    return {"issuer": context["verified_issuer"], "subject": context["stable_subject"],
            "account_id": context["account_id"]}


def execute_tool(request, _idempotency_key):
    # Pure, deterministic echo has no mutable effects to deduplicate.
    arguments = request["params"]["arguments"]
    if set(arguments) != {"text"} or not isinstance(arguments["text"], str):
        raise ValueError("echo requires one text argument")
    return {"jsonrpc": "2.0", "id": request.get("id"), "result": {
        "resultType": "complete", "content": [{"type": "text", "text": arguments["text"]}]}}


async def run(directory, request_id):
    token = os.environ.get("ATELLAGENT_REFERENCE_TOKEN", "")
    if not token or len(token) > 512 or not token.isascii():
        raise ValueError("Set a bounded ASCII ATELLAGENT_REFERENCE_TOKEN for this loopback example")
    config = await load_workload_config(directory, request_id, integration_type="mcp", packaging="sdk")
    runtime = ConnectedSDKRuntime(config, mcp_manifest=MANIFEST)
    mount_mcp_handler(runtime, execute_tool, consequential=False, target_idempotent=True)
    loop = asyncio.get_running_loop()

    class Listener(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Do not log credentials, tool arguments or results.

        def respond(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/mcp":
                return self.respond(404, {"error": "not_found"})
            authorization = self.headers.get("Authorization", "")
            if not authorization.isascii() or not hmac.compare_digest(authorization, "Bearer " + token):
                return self.respond(401, {"error": "native_authentication_required"})
            if any(name in self.headers for name in ("X-Atellagent-Connected-Capability", "X-Atellagent-Control-Directive")):
                return self.respond(403, {"error": "authority_intake_mismatch"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    return self.respond(413, {"error": "invalid_request_size"})
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                    raise ValueError()
                method = request.get("method")
                if method == "server/discover":
                    result = {"resultType": "complete", "supportedVersions": ["2026-07-28"],
                        "cacheScope": "private", "ttlMs": 0, "capabilities": {"tools": {}}}
                    return self.respond(200, {"jsonrpc": "2.0", "id": request.get("id"), "result": result})
                if method == "tools/list":
                    return self.respond(200, {"jsonrpc": "2.0", "id": request.get("id"),
                        "result": {"resultType": "complete", "cacheScope": "private", "ttlMs": 0,
                                   "tools": MANIFEST["tools"]}})
                if method != "tools/call":
                    raise ValueError()
                # Only this verified native context supplies caller identity.
                context = {"verified_issuer": "reference-server", "stable_subject": "demo-caller-1",
                           "account_id": "demo-account"}
                key = request.get("params", {}).get("_meta", {}).get("atellagent/idempotencyKey")
                task = asyncio.run_coroutine_threadsafe(runtime.handle_mcp_request(request,
                    idempotency_key=key, caller=caller_from_auth_context(context)), loop)
                return self.respond(200, task.result(timeout=80))
            except ActionDenied:
                return self.respond(403, {"error": "tool_call_not_authorized"})
            except Exception:
                return self.respond(400, {"error": "tool_call_failed"})

    server = ThreadingHTTPServer(("127.0.0.1", 8765), Listener)
    listener = None
    try:
        async with runtime:
            listener = threading.Thread(target=server.serve_forever, daemon=True)
            listener.start()
            print("Reference MCP server: http://127.0.0.1:8765/mcp", flush=True)
            await asyncio.Event().wait()
    finally:
        if listener is not None:
            await asyncio.to_thread(server.shutdown)
        server.server_close()


if __name__ == "__main__":
    args = options()
    asyncio.run(run(args.state_directory, args.request_id))
