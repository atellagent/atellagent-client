"""Native staging retains distinct integration keys and exact setup proof."""

import base64
from hashlib import sha256
import json
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import pytest

from atellagent_client.onboarding.native_claim_client import NativeClaimClient
from atellagent_client.onboarding.service_account_csr import service_account_csr, service_account_csr_facts
from atellagent_client.onboarding.errors import OnboardingClientError
from onboarding_support import identity, credential


@pytest.mark.asyncio
async def test_claim_binds_retained_key_and_exact_device_certificate(tmp_path, monkeypatch):
    device = identity(tmp_path / "device", tenant=uuid4(), device=uuid4(), owner=uuid4())
    leaf = credential(device)
    device.certificate_path.chmod(0o600)
    request, setup, account = uuid4(), uuid4(), uuid4()
    tls = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = service_account_csr(tls, tenant_id=device.tenant_id, service_account_id=account)
    assert pem == service_account_csr(tls, tenant_id=device.tenant_id, service_account_id=account)
    facts, nonces = service_account_csr_facts(pem), []
    def handler(call):
        assert "Authorization" not in call.headers and "X-Atellagent-Device-Signature" not in call.headers
        body = {"tenant_id": str(device.tenant_id), "device_id": str(device.device_id),
            "request_id": str(request), "revision": 1, "intent_sha256": "c" * 64,
            "device_certificate_sha256": sha256(leaf.public_bytes(serialization.Encoding.DER)).hexdigest(),
            "tls_public_key_sha256": facts.public_key_sha256, "csr_sha256": facts.csr_sha256}
        nonce = call.headers["X-Atellagent-Onboarding-Nonce"]
        material = "\n".join(("atellagent-onboarding-request-v1", str(setup), "POST", call.url.path,
            sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            call.headers["X-Atellagent-Onboarding-Timestamp"], nonce)).encode()
        device.proof_key.public_key().verify(base64.urlsafe_b64decode(
            call.headers["X-Atellagent-Onboarding-Signature"] + "=="), material)
        assert json.loads(call.content) == {"revision": 1, "intent_sha256": "c" * 64, "csr_pem": pem}
        nonces.append(nonce)
        return httpx.Response(200, json={"request_id": str(request), "revision": 1, "status": "issuing", "intent_sha256": "c" * 64})
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.device_http.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(handler)))
    async with NativeClaimClient(origin="https://device.example", device=device, setup_id=setup) as client:
        await client.stage(request_id=request, intent_sha256="c" * 64, csr_pem=pem)
        await client.stage(request_id=request, intent_sha256="c" * 64, csr_pem=pem)
        wrong = service_account_csr(tls, tenant_id=uuid4(), service_account_id=account)
        with pytest.raises(OnboardingClientError, match="tenant_mismatch"):
            await client.stage(request_id=request, intent_sha256="c" * 64, csr_pem=wrong)
        same_key = serialization.load_pem_private_key(device.private_key_path.read_bytes(), password=None)
        same = service_account_csr(same_key, tenant_id=device.tenant_id, service_account_id=account)
        with pytest.raises(OnboardingClientError, match="integration_key_not_separate"):
            await client.stage(request_id=request, intent_sha256="c" * 64, csr_pem=same)
    assert len(nonces) == len(set(nonces)) == 2
