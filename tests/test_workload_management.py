"""Workload setup persists exact keys/intent, without a desktop installation."""

import base64
from dataclasses import replace
from hashlib import sha256
import json
from uuid import uuid4

import httpx
import pytest

from atellagent_client.onboarding.device_login_models import ManagementTokens
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.proofs import canonical_json
from atellagent_client.onboarding.setup_draft import SetupDraft
from atellagent_client.onboarding.setup_keys import SetupKeys
from atellagent_client.onboarding.setup_management import SetupManagementClient
from atellagent_client.onboarding.workload_proposal import WorkloadProposal
from atellagent_client.onboarding.workload_setup_draft import WorkloadSetupDraft
from atellagent_client.onboarding.workload_setup_store import WorkloadSetupStore
from atellagent_client.onboarding.workload_request_receipt import WorkloadRequestReceipt


@pytest.fixture
def draft():
    setup = SetupDraft(uuid4(), uuid4(), uuid4(), "https://atellagent.com", "workload",
                       "Deployment", "0.9.1", None, SetupKeys.generate())
    proposal = WorkloadProposal(uuid4(), "Model deployment", "model", "sdk", ("model.inference",))
    return WorkloadSetupDraft(setup, proposal)


def receipt(draft, *, account_id=None, integration_id=None):
    result = {"id": str(draft.proposal.id), "kind": "workload", "status": "pending", "device_id": None,
        "revision": 1, "intent_sha256": "a" * 64, "service_account_id": str(account_id or uuid4()),
        "integration_id": str(integration_id or uuid4()), "expires_at": "2026-10-08T12:00:00Z", "claim_expires_at": None}
    intent = {"request_id": str(draft.proposal.id), "revision": 1, "kind": "workload",
        "tenant_id": str(draft.setup.tenant_id), "setup_operator_id": str(draft.setup.owner_user_id),
        "onboarding_session_id": str(draft.setup.operation_id),
        "service_account_id": result["service_account_id"], "integration_id": result["integration_id"],
        "tls_public_key_sha256": sha256(base64.b64decode(
            draft.setup.proposal()["tls_public_key_der_base64"])).hexdigest(),
        "proposal": draft.proposal.payload(), "ongoing_authority": "machine"}
    result["intent_sha256"] = sha256(canonical_json(intent)).hexdigest()
    return result


def test_private_store_reopens_the_same_keys_and_rejects_intent_change(tmp_path, draft):
    store = WorkloadSetupStore(tmp_path / "deployment")
    store.save_initial(draft)
    assert store.load(draft.proposal.id).encode() == draft.encode()
    assert "PRIVATE KEY" not in repr(draft)
    with pytest.raises(OnboardingClientError, match="already_saved"):
        store.save_initial(draft)
    changed = replace(draft, proposal=replace(draft.proposal, display_name="Other workload"))
    with pytest.raises(OnboardingClientError, match="intent_changed"):
        store.require_saved(changed)
    with pytest.raises(ValueError):
        WorkloadSetupDraft(replace(draft.setup, kind="device", platform="linux"), draft.proposal)


@pytest.mark.asyncio
async def test_saved_workload_submission_matches_the_server_proof_contract(tmp_path, draft, monkeypatch):
    store = WorkloadSetupStore(tmp_path / "workload")
    calls, nonces = [], []
    def handler(request):
        calls.append(request.method)
        assert request.headers["Authorization"] == "Bearer setup-access"
        if request.method == "GET":
            return httpx.Response(200, json={"user_id": str(draft.setup.owner_user_id), "selection_required": True,
                "memberships": [{"tenant_id": str(draft.setup.tenant_id), "tenant_name": "Customer", "can_approve": True}]})
        assert request.url.path == f"/api/client-onboarding/sessions/{draft.setup.operation_id}/workload"
        payload = json.loads(request.content)
        assert set(payload) == {"tenant_id", "proposal", "proof", "device_proof"}
        assert payload["device_proof"] is None
        assert payload["proposal"] == draft.proposal.payload()
        assert "PRIVATE KEY" not in str(payload) and "access_token" not in str(payload)
        body = {"tenant_id": payload["tenant_id"], "setup_operator_id": str(draft.setup.owner_user_id),
                "proposal": payload["proposal"]}
        proof = payload["proof"]
        material = "\n".join(("atellagent-onboarding-request-v1", str(draft.setup.operation_id), "POST",
            request.url.path, sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                separators=(",", ":")).encode()).hexdigest(), proof["timestamp"], proof["nonce"])).encode()
        draft.setup.keys.proof_key.public_key().verify(base64.urlsafe_b64decode(proof["signature"] + "=="), material)
        nonces.append(proof["nonce"])
        return httpx.Response(200, json=receipt(draft))
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.setup_management.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(handler)))
    async with SetupManagementClient(origin=draft.setup.platform_origin,
            tokens=ManagementTokens(300, "setup-access", None)) as client:
        with pytest.raises(OnboardingClientError):
            await client.submit_workload(draft=draft, store=store)
        assert calls == []
        store.save_initial(draft)
        for _ in range(2):
            result = await client.submit_workload(draft=draft, store=store)
            assert result.status == "pending" and result.device_id is None
    assert calls == ["GET", "POST", "GET", "POST"] and len(set(nonces)) == 2
    assert "setup-access" not in draft.encode().decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["operator", "tenant", "origin"])
async def test_changed_operator_tenant_or_origin_never_submits(tmp_path, draft, monkeypatch, change):
    if change == "origin":
        draft = replace(draft, setup=replace(draft.setup, platform_origin="https://other.example.com"))
    store = WorkloadSetupStore(tmp_path / "workload")
    store.save_initial(draft)
    posts = []
    def handler(request):
        if request.method != "GET":
            posts.append(request)
        return httpx.Response(200, json={"user_id": str(uuid4() if change == "operator" else draft.setup.owner_user_id),
            "selection_required": True, "memberships": []})
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.setup_management.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(handler)))
    async with SetupManagementClient(origin="https://atellagent.com", tokens=ManagementTokens(300, "setup-access", None)) as client:
        with pytest.raises(OnboardingClientError):
            await client.submit_workload(draft=draft, store=store)
    assert posts == []


@pytest.mark.parametrize("change", [{"device_id": str(uuid4())}, {"id": str(uuid4())},
    {"service_account_id": str(uuid4())}, {"integration_id": str(uuid4())},
    {"kind": "native_connection"}, {"revision": True}, {"status": "ready"},
    {"expires_at": "2026-10-08T12:00:00"}, {"intent_sha256": "bad"}, {"intent_sha256": "a" * 64},
    {"enrollment_token": "obsolete"}])
def test_receipt_rejects_another_identity_or_unsupported_authority(draft, change):
    with pytest.raises(OnboardingClientError):
        WorkloadRequestReceipt.from_payload({**receipt(draft), **change}, draft=draft)


def test_proposal_cannot_smuggle_native_host_or_provider_secret(draft):
    payload = draft.proposal.payload()
    assert WorkloadProposal.from_payload(payload) == draft.proposal
    for bad in ({**payload, "provider_key": "secret"},
                {**payload, "profile": {**payload["profile"], "native_host": "codex"}},
                {**payload, "capabilities": ["agent.control"]},
                {**payload, "offline_dispatch_deadline_seconds": True}):
        with pytest.raises(ValueError):
            WorkloadProposal.from_payload(bad)
