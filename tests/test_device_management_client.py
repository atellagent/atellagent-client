"""Management requests retain the admitted identity without a browser session."""

import base64
from hashlib import sha256
import json
from uuid import uuid4

import httpx
import pytest

from atellagent_client.onboarding import device as module
from atellagent_client.onboarding.errors import OnboardingClientError
from onboarding_support import identity, metadata
from atellagent_client.onboarding import discovery_snapshot as discovery_module


@pytest.mark.asyncio
async def test_stopped_client_cannot_be_reopened_by_credential_replacement(tmp_path, monkeypatch):
    device = identity(tmp_path / "device", tenant=uuid4(), device=uuid4(), owner=uuid4())
    pools = []
    def factory(**kwargs):
        pool = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"success": True})))
        pools.append(pool)
        return pool
    monkeypatch.setattr(module, "create_device_http_client", factory)
    client = module.DeviceManagementClient(origin="https://device.example", device=device)
    await client.aclose()
    with pytest.raises(OnboardingClientError, match="device_runtime_stopped"):
        await client.replace_credentials(device)
    with pytest.raises(OnboardingClientError, match="device_runtime_stopped"):
        await client._request("GET", "/devices/self/status")
    assert all(pool.is_closed for pool in pools)


@pytest.mark.asyncio
async def test_preparation_selector_is_signed_with_device_key_without_browser_auth(tmp_path, monkeypatch):
    from test_device_preparations import preparation
    device = identity(tmp_path / "device", tenant=uuid4(), device=uuid4(), owner=uuid4())
    value = preparation()
    from uuid import UUID
    identifier = UUID(value["assignment_id"])
    def handler(request):
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        target = request.url.path + "?" + request.url.query.decode()
        assert target == f"/devices/self/preparations?assignment_id={identifier}"
        material = "\n".join(("atellagent-device-request-v1", str(device.device_id), request.method,
            target, sha256(request.content).hexdigest(), request.headers["X-Atellagent-Device-Timestamp"],
            request.headers["X-Atellagent-Device-Nonce"])).encode()
        device.proof_key.public_key().verify(base64.urlsafe_b64decode(
            request.headers["X-Atellagent-Device-Signature"] + "=="), material)
        return httpx.Response(200, json=dict(preparations=[value], next_after=None))
    monkeypatch.setattr(module, "create_device_http_client", lambda **kwargs:
        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    async with module.DeviceManagementClient(origin="https://device.example", device=device) as client:
        page = await client.preparations(assignment_id=identifier)
        assert page.preparations[0].assignment_id == identifier


@pytest.mark.asyncio
async def test_status_name_and_reporting_use_exact_device_proof_without_browser_auth(tmp_path, monkeypatch):
    device = identity(tmp_path / "device", tenant=uuid4(), device=uuid4(), owner=uuid4())
    nonces = []

    def handler(request):
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        nonce = request.headers["X-Atellagent-Device-Nonce"]
        nonces.append(nonce)
        material = "\n".join(("atellagent-device-request-v1", str(device.device_id), request.method,
            request.url.path, sha256(request.content).hexdigest(),
            request.headers["X-Atellagent-Device-Timestamp"], nonce)).encode()
        device.proof_key.public_key().verify(base64.urlsafe_b64decode(
            request.headers["X-Atellagent-Device-Signature"] + "=="), material)
        if request.method == "GET":
            return httpx.Response(200, json=metadata(device))
        return httpx.Response(200, json={"success": True, **json.loads(request.content)})

    monkeypatch.setattr(module, "create_device_http_client", lambda **kwargs:
        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    async with module.DeviceManagementClient(origin="https://device.example", device=device) as client:
        assert (await client.status()).owner_user_id == device.owner_user_id
        await client.rename("My laptop")
        await client.set_reporting(False)
        await client.set_reporting(True)
    assert len(nonces) == len(set(nonces)) == 4


@pytest.mark.asyncio
async def test_status_cannot_adopt_a_different_device_owner(tmp_path, monkeypatch):
    device = identity(tmp_path / "device", tenant=uuid4(), device=uuid4(), owner=uuid4())
    response = {**metadata(device), "owner_user_id": str(uuid4())}
    monkeypatch.setattr(module, "create_device_http_client", lambda **kwargs:
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))))
    async with module.DeviceManagementClient(origin="https://device.example", device=device) as client:
        with pytest.raises(OnboardingClientError) as error:
            await client.status()
        assert error.value.code == "device_identity_mismatch"


def test_discovery_projection_is_bounded_and_does_not_leak_config(tmp_path, monkeypatch):
    def scan(**kwargs):
        assert kwargs == {"home": tmp_path}
        item = {"host": "codex", "config_files": [{"path": "/secret/config"}],
            "mcp_servers": [{"name": "bad/name", "transport": "http"}] + [
                {"name": f"tool-{index}", "transport": "stdio", "command": "secret",
                 "url": "https://secret.example", "args": ["token"]} for index in range(100)]}
        return {"hosts": [item, item, {"host": "unsupported"}]}
    monkeypatch.setattr(discovery_module, "discover", scan)
    payload = discovery_module.discovery_snapshot(home=tmp_path)
    assert len(payload["hosts"]) == 1
    assert len(payload["hosts"][0]["mcp_servers"]) == 64
    assert payload["hosts"][0]["mcp_servers"][0] == {"name": "tool-0", "transport": "stdio"}
    assert "secret" not in json.dumps(payload) and "token" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_discovery_upload_and_removal_need_only_device_identity(tmp_path, monkeypatch):
    device = identity(tmp_path / "device", tenant=uuid4(), device=uuid4(), owner=uuid4())
    monkeypatch.setattr(discovery_module, "discover", lambda **kwargs: {"hosts": []})
    requests = []
    def handler(request):
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        assert request.url.path == "/devices/self/discovery"
        material = "\n".join(("atellagent-device-request-v1", str(device.device_id), request.method,
            request.url.path, sha256(request.content).hexdigest(),
            request.headers["X-Atellagent-Device-Timestamp"],
            request.headers["X-Atellagent-Device-Nonce"])).encode()
        device.proof_key.public_key().verify(base64.urlsafe_b64decode(
            request.headers["X-Atellagent-Device-Signature"] + "=="), material)
        requests.append(request)
        return httpx.Response(200, json={"success": True})
    monkeypatch.setattr(module, "create_device_http_client", lambda **kwargs:
        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    async with module.DeviceManagementClient(origin="https://device.example", device=device) as client:
        await client.report_discovery()
        await client.remove_discovery()
    assert json.loads(requests[0].content)["hosts"] == []
    assert requests[1].method == "DELETE" and requests[1].content == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{"success": False}, {"success": 1}, {"success": True, "extra": 1}])
async def test_discovery_requires_exact_ack(tmp_path, monkeypatch, response):
    device = identity(tmp_path / "device", tenant=uuid4(), device=uuid4(), owner=uuid4())
    monkeypatch.setattr(module, "create_device_http_client", lambda **kwargs:
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))))
    async with module.DeviceManagementClient(origin="https://device.example", device=device) as client:
        with pytest.raises(OnboardingClientError) as error:
            await client.remove_discovery()
        assert error.value.code == "device_response_invalid"
