"""Direct intake verifies actual signatures before using the shared mounted tool."""

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
import pytest

from atellagent_client.connected import ConnectedDelivery, ConnectedParticipant, mount_mcp_handler
from atellagent_client.governance.actions import ActionDenied
from connected_runtime_support import _config
from service_account_certificate_support import certificate_case


@pytest.fixture
def runtime(tmp_path):
    case = certificate_case()
    certificate = tmp_path / "client.crt"
    certificate.write_bytes(case.leaf.public_bytes(serialization.Encoding.PEM))
    config = _config(integration_type="mcp")
    config.cert_path = str(certificate)
    config.mcp_descriptor_path_template = "/v1/connected-runtimes/instances/{instance_id}/descriptors/mcp"
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk["kid"] = "resource-test"
    state = SimpleNamespace(mode="allow", response_allowed=True, effects=[], requests=[], receipts=[])

    def respond(request):
        path = request.url.path
        def response(status, payload):
            return httpx.Response(status, json=payload, extensions={"http_version": b"HTTP/2"})
        if path.endswith("/jwks/execution-tokens"):
            return response(200, {"keys": [jwk]})
        payload = json.loads(request.content)
        state.requests.append((path, payload))
        if path.endswith("/preflight"):
            if state.mode == "unavailable":
                raise httpx.ConnectError("synthetic unavailable gateway")
            if state.mode == "blocked":
                return response(403, {"code": "direct_mcp_clients_blocked"})
            key, decision = "mcp:" + config.integration_id + ":logical-action", str(uuid4())
            invocation = {"tool_name": "echo", "arguments": payload["arguments"]}
            claims = {"iss": "gateway", "aud": ["atellagent-client-pep"], "jti": str(uuid4()),
                "exp": int(datetime.now(timezone.utc).timestamp()) + 60,
                "schema_version": "v1", "typ": "atellagent_control_directive", "mode": "enforce",
                "allowed": True, "action": "echo", "integration_type": "mcp", "correlation_id": key,
                "raw_action_fingerprint": sha256(json.dumps(invocation, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "policy_decision_id": decision, "tenant_id": config.tenant_id,
                "execution_id": None, "workspace_id": None, "evidence_requirements": [],
                "descriptor": {"id": "mcp:" + config.integration_id, "revision": 1, "digest": "a" * 64,
                    "action_id": config.service_account_id + ":echo", "execution_route": "governed_mcp",
                    "integration_id": config.integration_id}}
            if state.mode == "wrong_descriptor":
                claims["descriptor"]["digest"] = "b" * 64
            if state.mode == "wrong_arguments":
                claims["raw_action_fingerprint"] = "c" * 64
            if state.mode == "wrong_scope":
                claims["tenant_id"] = str(uuid4())
            signer = private if state.mode != "wrong_signature" else rsa.generate_private_key(public_exponent=65537, key_size=2048)
            token = jwt.encode(claims, signer, algorithm="RS256", headers={"kid": "resource-test"})
            return response(200, {"success": True, "action_key": key, "decision": {"allowed": True, "decision_id": decision},
                                  "control_directive": token})
        if path.endswith("/postflight"):
            state.receipts.append(payload)
            return response(200, {"success": True, "action_key": payload["action_key"],
                                  "decision": {"allowed": state.response_allowed, "decision_id": str(uuid4())}})
        raise AssertionError("Unexpected transport path")

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    class Manager:
        http2 = True
        async def get_async_client(self):
            return client
    class Session:
        base_url = "https://mtls.gateway.example"
        http_client_manager = Manager()
        async def request_authenticated(self, method, url, **kwargs):
            return await client.request(method, url, **kwargs)
    participant = ConnectedParticipant(config, session=Session(), mcp_manifest={"tools": [{"name": "echo"}]})
    participant._started = True
    participant._instance_id = str(uuid4())
    participant._mcp_publication = {"manifest_hash": "a" * 64, "descriptor_revision": 1}

    def handler(request, key):
        state.effects.append((request["params"]["arguments"], key))
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": {"content": [{"type": "text", "text": "ok"}]}}
    mount_mcp_handler(participant, handler, consequential=True, target_idempotent=True)
    try:
        yield participant, state
    finally:
        asyncio.run(client.aclose())


def request():
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "echo", "arguments": {"text": "safe"}}}


@pytest.mark.asyncio
async def test_direct_verifies_signed_binding_then_uses_same_handler_as_connected(runtime):
    participant, state = runtime
    caller = {"issuer": "native-provider", "subject": "opaque-caller", "account_id": "customer"}
    await participant.handle_mcp_request(request(), idempotency_key="logical-1", caller=caller)
    assert len(state.effects) == 1 and len(state.receipts) == 1
    assert state.receipts[0]["caller"] == caller and state.receipts[0]["success"] is True
    assert state.effects[0][1] == state.receipts[0]["action_key"]
    state.mode = "blocked"
    delivery = ConnectedDelivery(message_id=str(uuid4()), kind="action", operation="mcp.tools.call",
        execution_id=None, execution_attempt_id=None, idempotency_key="connected-1",
        payload_schema="atellagent.connected.mcp.v1", payload={"request": request()},
        delivery_attempt=1, lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60))
    result = await participant._handler_for_operation("mcp.tools.call").handler(delivery, None)
    assert result.terminal_status == "succeeded"
    assert len(state.effects) == 2 and len(state.receipts) == 1
    assert len([path for path, _ in state.requests if path.endswith("/preflight")]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["blocked", "unavailable", "wrong_signature", "wrong_descriptor", "wrong_arguments", "wrong_scope"])
async def test_missing_or_mismatched_authority_never_reaches_handler(runtime, failure):
    participant, state = runtime
    state.mode = failure
    with pytest.raises(ActionDenied):
        await participant.handle_mcp_request(request(), idempotency_key="logical-1")
    assert state.effects == []


@pytest.mark.asyncio
async def test_response_policy_withholds_data_after_recording_one_execution(runtime):
    participant, state = runtime
    state.response_allowed = False
    with pytest.raises(ActionDenied, match="mcp_response_not_authorized"):
        await participant.handle_mcp_request(request(), idempotency_key="logical-1")
    assert len(state.effects) == 1 and len(state.receipts) == 1


@pytest.mark.asyncio
async def test_request_metadata_cannot_supply_an_alternate_authorization(runtime):
    participant, state = runtime
    payload = request()
    payload["params"]["_meta"] = {"atellagent/controlDirective": "invalid"}
    with pytest.raises(ActionDenied, match="mcp_authority_intake_mismatch"):
        await participant.handle_mcp_request(payload, idempotency_key="logical-1")
    assert state.effects == [] and state.requests == []
