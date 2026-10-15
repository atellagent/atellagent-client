"""Local activation receipts never bypass live device/user/membership authority."""

import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from atellagent_client.onboarding.device_activation_receipt import save_activation_receipt
from atellagent_client.onboarding.device_resume import resume_stored_device
from atellagent_client.onboarding.errors import OnboardingClientError
from test_device_credential_store import case, save


def fixture(tmp_path, monkeypatch, *, reporting="disabled", response_status=200, wrong_owner=False, activate=True):
    data = case(tmp_path)
    draft, receipt, trust, credential, setups, store = data
    stored = save(data)
    if activate:
        save_activation_receipt(draft, stored)
    calls = []
    factory = httpx.AsyncClient
    async def respond(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/devices/self/status"
        assert "Authorization" not in request.headers
        assert request.headers["X-Atellagent-Device-Id"] == str(receipt.device_id)
        if response_status != 200:
            return httpx.Response(response_status, json={"error": {"code": "onboarding_permission_denied"}})
        return httpx.Response(200, json={"device_id": str(receipt.device_id), "tenant_id": str(draft.tenant_id),
            "owner_user_id": str(uuid4() if wrong_owner else draft.owner_user_id), "device_name": "My Mac",
            "status": "active", "tenant_name": "Test organization", "discovery_reporting_status": reporting,
            "certificate_expires_at": credential.expires_at.isoformat(), "discovery_observed_at": None,
            "discovery_fresh": False, "server_time": datetime.now(timezone.utc).isoformat()})
    monkeypatch.setattr("atellagent_client.onboarding.device_http.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(respond)))
    arguments = dict(draft=draft, receipt=receipt, trust=trust, setup_store=setups,
        credential_store=store, fingerprint=credential.certificate_sha256)
    return arguments, stored, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("reporting", ["enabled", "disabled"])
async def test_resume_checks_live_status_and_respects_current_preference(tmp_path, monkeypatch, reporting):
    arguments, _, calls = fixture(tmp_path, monkeypatch, reporting=reporting)
    current = await resume_stored_device(**arguments)
    assert current.device.owner_user_id == arguments["draft"].owner_user_id
    assert current.reporting_enabled is (reporting == "enabled")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_revoked_device_is_not_admitted_by_saved_receipt(tmp_path, monkeypatch):
    arguments, stored, calls = fixture(tmp_path, monkeypatch, response_status=403)
    with pytest.raises(OnboardingClientError, match="permission_denied"):
        await resume_stored_device(**arguments)
    assert len(calls) == 1 and (stored.certificate_path.parent / "activation.json").exists()


@pytest.mark.asyncio
async def test_changed_server_owner_is_rejected(tmp_path, monkeypatch):
    arguments, _, _ = fixture(tmp_path, monkeypatch, wrong_owner=True)
    with pytest.raises(OnboardingClientError, match="identity_mismatch"):
        await resume_stored_device(**arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["missing", "owner", "revision"])
async def test_incomplete_or_changed_local_receipt_cannot_resume(tmp_path, monkeypatch, broken):
    arguments, stored, calls = fixture(tmp_path, monkeypatch, activate=broken != "missing")
    path = stored.certificate_path.parent / "activation.json"
    if broken != "missing":
        value = json.loads(path.read_bytes())
        value["owner_user_id" if broken == "owner" else "revision"] = str(uuid4()) if broken == "owner" else True
        path.write_text(json.dumps(value))
    with pytest.raises(OnboardingClientError, match="activation_receipt"):
        await resume_stored_device(**arguments)
    assert calls == []
