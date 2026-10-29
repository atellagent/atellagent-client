"""Actual retained keys and CA-signed leaves; no continuing human OAuth."""

import base64
from dataclasses import replace
from hashlib import sha256
import json
from uuid import uuid4

import httpx
import pytest

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.service_account_csr import service_account_csr
from atellagent_client.onboarding.setup_keys import SetupKeys
from atellagent_client.onboarding.workload_proof_client import WorkloadProofClient
from atellagent_client.onboarding.workload_setup_store import WorkloadSetupStore
from atellagent_client.onboarding.workload_request_receipt import WorkloadRequestReceipt
from service_account_certificate_support import certificate_case
from test_workload_management import draft, receipt


@pytest.fixture
def case(tmp_path, draft):
    certificate = certificate_case()
    args = certificate.args
    draft = replace(draft, setup=replace(draft.setup, platform_origin=args["platform_origin"],
        tenant_id=args["tenant_id"], keys=SetupKeys(draft.setup.keys.proof_key, certificate.key)))
    payload = receipt(draft, account_id=args["service_account_id"], integration_id=args["integration_id"])
    store = WorkloadSetupStore(tmp_path / "workload")
    store.save_initial(draft)
    return draft, store, payload, certificate


@pytest.mark.asyncio
async def test_exact_status_csr_and_certificate_proofs_survive_without_login(case, monkeypatch):
    draft, store, payload, certificate = case
    args, calls, nonces = certificate.args, [], []
    responses = [{**payload, "status": "approved"}, {"id": payload["id"], "status": "issuing", "revision": 1,
        "expires_at": payload["expires_at"], "claim_expires_at": "2026-10-08T11:59:00Z"},
        {"status": "issuing"}, args["payload"]]
    def handler(request):
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        calls.append(request.url.path)
        if request.method == "POST":
            body = json.loads(request.content)
            assert set(body) == {"revision", "intent_sha256", "csr_pem", "proof"}
            from atellagent_client.onboarding.service_account_csr import service_account_csr_facts
            csr = service_account_csr_facts(body["csr_pem"])
            preimage = {"tenant_id": str(draft.setup.tenant_id), "request_id": payload["id"], "revision": 1,
                "intent_sha256": payload["intent_sha256"], "tls_public_key_sha256": csr.public_key_sha256,
                "csr_sha256": csr.csr_sha256}
            digest = sha256(json.dumps(preimage, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            proof = body["proof"]
        else:
            assert request.content == b""
            digest = sha256(b"").hexdigest()
            proof = {name: request.headers[f"X-Atellagent-Onboarding-{name.title()}"]
                     for name in ("timestamp", "nonce", "signature")}
        material = "\n".join(("atellagent-onboarding-request-v1", str(draft.setup.operation_id), request.method,
            request.url.path, digest, proof["timestamp"], proof["nonce"])).encode()
        draft.setup.keys.proof_key.public_key().verify(base64.urlsafe_b64decode(proof["signature"] + "=="), material)
        nonces.append(proof["nonce"])
        return httpx.Response(200, json=responses.pop(0))
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.workload_proof_client.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(handler)))
    async with WorkloadProofClient(draft=draft, store=store,
            receipt=WorkloadRequestReceipt.from_payload(payload, draft=draft)) as client:
        assert (await client.status()).status == "approved"
        await client.stage(csr_pem=args["csr_pem"])
        assert await client.certificate(csr_pem=args["csr_pem"], trust=args["trust"]) is None
        credential = await client.certificate(csr_pem=args["csr_pem"], trust=args["trust"])
        assert credential.service_account_id == args["service_account_id"]
    assert len(calls) == 4 and len(set(nonces)) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["tenant", "account", "key"])
async def test_foreign_csr_is_rejected_before_network(case, monkeypatch, change):
    draft, store, payload, certificate = case
    foreign = certificate_case()
    csr = service_account_csr(foreign.key if change == "key" else draft.setup.keys.tls_key,
        tenant_id=uuid4() if change == "tenant" else draft.setup.tenant_id,
        service_account_id=uuid4() if change == "account" else certificate.args["service_account_id"])
    calls = []
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.workload_proof_client.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(lambda request: calls.append(request))))
    async with WorkloadProofClient(draft=draft, store=store,
            receipt=WorkloadRequestReceipt.from_payload(payload, draft=draft)) as client:
        with pytest.raises(OnboardingClientError):
            await client.stage(csr_pem=csr)
    assert calls == []


@pytest.mark.asyncio
async def test_poll_cannot_replace_account_by_rehashing_server_intent(case, monkeypatch):
    draft, store, payload, certificate = case
    changed = receipt(draft)
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.workload_proof_client.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=changed))))
    async with WorkloadProofClient(draft=draft, store=store,
            receipt=WorkloadRequestReceipt.from_payload(payload, draft=draft)) as client:
        with pytest.raises(OnboardingClientError):
            await client.status()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["foreign-trust", "wrong-account", "false-ready"])
async def test_delivery_cannot_adopt_foreign_trust_or_false_readiness(case, monkeypatch, change):
    draft, store, payload, certificate = case
    trust = certificate_case().args["trust"] if change == "foreign-trust" else certificate.args["trust"]
    delivered = dict(certificate.args["payload"])
    delivered.update({"service_account_id": str(uuid4())} if change == "wrong-account" else
                     {"status": "connected"} if change == "false-ready" else {})
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.workload_proof_client.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=delivered))))
    async with WorkloadProofClient(draft=draft, store=store,
            receipt=WorkloadRequestReceipt.from_payload(payload, draft=draft)) as client:
        with pytest.raises(OnboardingClientError):
            await client.certificate(csr_pem=certificate.args["csr_pem"], trust=trust)
