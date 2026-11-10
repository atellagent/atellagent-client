"""Assignment pages retain exact device-bound intent and signed cursor targets."""

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import pytest

from atellagent_client.onboarding import device as module
from atellagent_client.onboarding.device_assignments import DeviceAssignmentPage
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.proofs import canonical_json
from onboarding_support import identity


@pytest.fixture
def assigned(tmp_path):
    device = identity(tmp_path / "device", tenant=uuid4(), device=uuid4(), owner=uuid4())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    proposal = {"id": str(UUID(int=1)), "display_name": "My integration",
        "profile": {"account_type": "agent", "packaging": "sdk", "native_host": "codex"},
        "enforcement_mode": "enforce", "coverage_posture": "strict",
        "tls_public_key_der_base64": base64.b64encode(key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()}
    intent = {field: str(uuid4()) for field in (
        "service_account_id", "integration_id", "agent_principal_id", "binding_id", "controller_profile_id")}
    intent.update(request_id=proposal["id"], revision=1, proposal=proposal,
        tenant_id=str(device.tenant_id), owner_user_id=str(device.owner_user_id), device_id=str(device.device_id),
        capabilities=["agent.control"], host_adapter_version="atellagent.host-hooks.v1", delegation_lifetime="until_revoked")
    item = {key: value for key, value in intent.items() if key not in ("tenant_id", "owner_user_id", "device_id")}
    item.update(onboarding_session_id=str(uuid4()), status="approved",
        intent_sha256=sha256(canonical_json(intent)).hexdigest(),
        expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), claim_expires_at=None,
        owner_consented_at=None)
    return device, {"assignments": [item], "next_after": None}


@pytest.mark.asyncio
async def test_assignment_request_signs_exact_cursor_without_browser_auth(assigned, monkeypatch):
    device, payload = assigned
    nonces = []
    def handler(request):
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        assert request.method == "GET" and request.content == b""
        assert str(request.url).endswith("/devices/self/assignments?after=" + str(UUID(int=0)))
        nonce = request.headers["X-Atellagent-Device-Nonce"]
        material = "\n".join(("atellagent-device-request-v1", str(device.device_id), "GET",
            "/devices/self/assignments?after=" + str(UUID(int=0)), sha256(b"").hexdigest(),
            request.headers["X-Atellagent-Device-Timestamp"], nonce)).encode()
        device.proof_key.public_key().verify(base64.urlsafe_b64decode(
            request.headers["X-Atellagent-Device-Signature"] + "=="), material)
        nonces.append(nonce)
        return httpx.Response(200, json=payload)
    monkeypatch.setattr(module, "create_device_http_client", lambda **kwargs:
        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    async with module.DeviceManagementClient(origin="https://device.example", device=device) as client:
        for _ in range(2):
            page = await client.assignments(after=UUID(int=0))
            assert page.assignments[0].request_id == UUID(int=1)
            assert page.assignments[0].status == "approved"
    assert len(set(nonces)) == 2


@pytest.mark.parametrize("change", ["owner", "key", "unknown", "duplicate", "cursor", "revision", "time", "oversize"])
def test_malformed_or_foreign_assignment_is_not_adopted(assigned, change):
    device, payload = assigned
    item = payload["assignments"][0]
    if change == "owner":
        device = identity(device.certificate_path.parent / "foreign", tenant=device.tenant_id,
                          device=device.device_id, owner=uuid4())
    elif change == "key":
        item["proposal"]["tls_public_key_der_base64"] = "invalid"
    elif change == "unknown":
        item["secret"] = "do not adopt"
    elif change == "duplicate":
        payload["assignments"].append(item)
    elif change == "cursor":
        payload["next_after"] = item["request_id"]
    elif change == "revision":
        item["revision"] = True
    elif change == "time":
        item["expires_at"] = "2026-10-01T00:00:00"
    else:
        payload["assignments"] *= 9
    with pytest.raises(OnboardingClientError, match="device_assignment_response_invalid"):
        DeviceAssignmentPage.from_payload(payload, device=device)


def test_full_page_advances_only_at_exact_final_request(assigned):
    device, payload = assigned
    original = payload["assignments"][0]
    items = []
    for index in range(1, 9):
        item = deepcopy(original)
        item["request_id"] = item["proposal"]["id"] = str(UUID(int=index))
        intent = {key: value for key, value in item.items() if key not in (
            "onboarding_session_id", "status", "intent_sha256", "expires_at", "claim_expires_at", "owner_consented_at")}
        intent.update(tenant_id=str(device.tenant_id), device_id=str(device.device_id),
                      owner_user_id=str(device.owner_user_id))
        item["intent_sha256"] = sha256(canonical_json(intent)).hexdigest()
        items.append(item)
    payload = {"assignments": items, "next_after": str(UUID(int=8))}
    page = DeviceAssignmentPage.from_payload(payload, device=device, after=UUID(int=0))
    assert page.next_after == UUID(int=8) and len(page.assignments) == 8
    with pytest.raises(OnboardingClientError):
        DeviceAssignmentPage.from_payload(payload, device=device, after=UUID(int=1))
    payload["next_after"] = str(UUID(int=7))
    with pytest.raises(OnboardingClientError):
        DeviceAssignmentPage.from_payload(payload, device=device)


def test_recorded_consent_is_metadata_not_an_approval_or_changed_intent(assigned):
    device, payload = assigned
    item = payload["assignments"][0]
    item["status"] = "pending"
    original_hash = item["intent_sha256"]
    item["owner_consented_at"] = datetime.now(timezone.utc).isoformat()
    page = DeviceAssignmentPage.from_payload(payload, device=device)
    assert page.assignments[0].status == "pending" and page.assignments[0].intent_sha256 == original_hash
    assert page.assignments[0].owner_consented_at is not None
    assert page.assignments[0].payload() == item


@pytest.mark.parametrize("invalid", ["missing", "naive", "boolean"])
def test_consent_status_contract_is_required_and_aware(assigned, invalid):
    device, payload = assigned
    item = payload["assignments"][0]
    if invalid == "missing":
        del item["owner_consented_at"]
    else:
        item["owner_consented_at"] = "2026-10-01T00:00:00" if invalid == "naive" else True
    with pytest.raises(OnboardingClientError):
        DeviceAssignmentPage.from_payload(payload, device=device)
