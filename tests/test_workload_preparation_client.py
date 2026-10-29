"""Reviewed workload pickup uses retained keys and immutable private drafts."""

import base64
from hashlib import sha256
import json
from uuid import UUID, uuid4

import httpx
import pytest

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.proofs import canonical_json
from atellagent_client.onboarding.setup_store import SetupStore
from atellagent_client.onboarding.workload_preparation_client import WorkloadPreparationClient
from atellagent_client.onboarding.workload_preparation_page import WorkloadPreparationPage
from atellagent_client.onboarding.workload_setup_store import WorkloadSetupStore
from test_workload_management import draft, receipt


@pytest.fixture
def case(tmp_path, draft):
    setup_store, store = SetupStore(tmp_path / "setups"), WorkloadSetupStore(tmp_path / "workloads")
    setup_store.save_initial(draft.setup)
    settings = draft.proposal.payload(); settings.pop("id")
    preparation = dict(id=str(draft.proposal.id), settings=settings, settings_sha256=sha256(canonical_json(settings)).hexdigest(),
        status="awaiting_proposal", proposed_request_id=None, expires_at="2026-10-08T12:00:00Z")
    keys = draft.setup.proposal()
    page = dict(session_id=str(draft.setup.operation_id), tenant_id=str(draft.setup.tenant_id), operator_user_id=str(draft.setup.owner_user_id),
        proof_public_key_sha256=sha256(base64.urlsafe_b64decode(keys["proof_public_key_raw"] + "=")).hexdigest(),
        tls_public_key_sha256=sha256(base64.b64decode(keys["tls_public_key_der_base64"])).hexdigest(),
        preparations=[preparation], next_after=None)
    return draft, setup_store, store, page


def verify_request(request, setup, body=None):
    timestamp, nonce, signature = (request.headers[f"X-Atellagent-Onboarding-{name}"]
        for name in ("Timestamp", "Nonce", "Signature")) if request.method == "GET" else (
        json.loads(request.content)["proof"][name] for name in ("timestamp", "nonce", "signature"))
    path = request.url.raw_path.decode()
    digest = sha256(canonical_json(body) if body is not None else b"").hexdigest()
    material = "\n".join(("atellagent-onboarding-request-v1", str(setup.operation_id), request.method, path, digest, timestamp, nonce)).encode()
    setup.keys.proof_key.public_key().verify(base64.urlsafe_b64decode(signature + "=="), material)


@pytest.mark.asyncio
async def test_real_signed_listing_and_retry_keep_keys_and_draft(case, monkeypatch):
    draft, setups, store, page = case
    calls = []
    def respond(request):
        assert "authorization" not in request.headers
        calls.append(request)
        if request.method == "GET":
            verify_request(request, draft.setup)
            return httpx.Response(200, json=page)
        payload = json.loads(request.content)
        body = {name: payload[name] for name in ("tenant_id", "proposal")}
        verify_request(request, draft.setup, body)
        assert store.require_saved(draft).encode() == draft.encode()
        if len(calls) == 2: return httpx.Response(503, json={"error": {"code": "onboarding_storage_unavailable", "retryable": True}})
        return httpx.Response(200, json=receipt(draft))
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.workload_preparation_client.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(respond)))
    async with WorkloadPreparationClient(setup=draft.setup, setup_store=setups) as client:
        preparation = (await client.preparations(after=UUID(int=draft.proposal.id.int - 1))).preparations[0]
        with pytest.raises(OnboardingClientError): await client.pickup(preparation, store=store)
        saved, result = await client.pickup(preparation, store=store)
        assert saved.encode() == draft.encode() and result.id == draft.proposal.id
    assert json.loads(calls[1].content)["proposal"] == json.loads(calls[2].content)["proposal"]
    assert json.loads(calls[1].content)["proof"]["nonce"] != json.loads(calls[2].content)["proof"]["nonce"]


@pytest.mark.parametrize("change", ["session_id", "tenant_id", "operator_user_id", "proof_public_key_sha256",
    "tls_public_key_sha256", "digest", "extra", "settings-id", "link", "cursor", "duplicate", "oversize"])
def test_changed_or_unbounded_page_rejected(case, change):
    draft, _, _, page = case
    if change in {"session_id", "tenant_id", "operator_user_id"}: page[change] = str(uuid4())
    elif change in {"proof_public_key_sha256", "tls_public_key_sha256"}: page[change] = "c" * 64
    elif change == "digest": page["preparations"][0]["settings_sha256"] = "c" * 64
    elif change == "extra": page["token"] = "not allowed"
    elif change == "settings-id": page["preparations"][0]["settings"]["id"] = str(uuid4())
    elif change == "link": page["preparations"][0]["proposed_request_id"] = str(uuid4())
    elif change == "cursor": page["next_after"] = str(uuid4())
    elif change == "duplicate": page["preparations"] *= 2
    else: page["preparations"] *= 26
    with pytest.raises(OnboardingClientError): WorkloadPreparationPage.from_payload(page, setup=draft.setup)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["saved-settings", "cancelled", "receipt"])
async def test_pickup_cannot_replace_local_intent_or_accept_false_receipt(case, monkeypatch, change):
    from dataclasses import replace
    draft, setups, store, page = case
    preparation = WorkloadPreparationPage.from_payload(page, setup=draft.setup).preparations[0]
    if change == "saved-settings":
        store.save_initial(replace(draft, proposal=replace(draft.proposal, display_name="Other")))
    elif change == "cancelled": preparation = replace(preparation, status="cancelled")
    calls = []
    def respond(request):
        calls.append(request)
        payload = receipt(draft); payload["intent_sha256"] = "c" * 64
        return httpx.Response(200, json=payload)
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.workload_preparation_client.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(respond)))
    async with WorkloadPreparationClient(setup=draft.setup, setup_store=setups) as client:
        with pytest.raises(OnboardingClientError): await client.pickup(preparation, store=store)
    assert len(calls) == (1 if change == "receipt" else 0)


@pytest.mark.asyncio
async def test_workload_context_pins_pickup_receipt(case, tmp_path, monkeypatch):
    from atellagent_client.onboarding.workload_context import WorkloadContext
    draft, _, _, page = case
    context = WorkloadContext(tmp_path / "context")
    context.setups.save_initial(draft.setup)
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.workload_preparation_client.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(lambda request:
            httpx.Response(200, json=page if request.method == "GET" else receipt(draft)))))
    preparation = (await context.preparations(draft.setup)).preparations[0]
    result = await context.pickup_preparation(draft.setup, preparation)
    assert context.receipts.load(draft).payload() == result.payload()
