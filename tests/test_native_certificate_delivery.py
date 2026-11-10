"""Exact setup proof delivers verified public material, never a readiness claim."""

import base64
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
import pytest

from atellagent_client.onboarding.device_assignments import DeviceAssignment
from atellagent_client.onboarding.native_proposal import NativeProposal
from atellagent_client.onboarding.native_certificate_delivery import fetch_native_certificate
from atellagent_client.onboarding.errors import OnboardingClientError
from service_account_certificate_support import certificate_case


@pytest.mark.asyncio
async def test_delivery_proves_original_setup_and_verifies_retained_integration():
    case, setup, request = certificate_case(), uuid4(), uuid4()
    args, nonces = case.args, []
    device = SimpleNamespace(tenant_id=args["tenant_id"], proof_key=Ed25519PrivateKey.generate())
    proposal = NativeProposal(request, "My host", "sdk", "codex", "enforce", "strict",
                             base64.b64encode(args["approved_public_key_der"]).decode())
    assignment = DeviceAssignment(request, setup, args["service_account_id"], args["integration_id"],
        uuid4(), uuid4(), uuid4(), 1, "issued", "a" * 64, proposal,
        "atellagent.host-hooks.v1", "until_revoked", ("agent.control",), case.leaf.not_valid_after_utc, None, None)
    responses = [{"status": "issuing"}, args["payload"]]
    def handler(call):
        assert call.method == "GET" and call.content == b""
        assert "Authorization" not in call.headers and "Cookie" not in call.headers
        nonce = call.headers["X-Atellagent-Onboarding-Nonce"]
        material = "\n".join(("atellagent-onboarding-request-v1", str(setup), "GET", call.url.path,
            sha256(b"").hexdigest(), call.headers["X-Atellagent-Onboarding-Timestamp"], nonce)).encode()
        device.proof_key.public_key().verify(base64.urlsafe_b64decode(
            call.headers["X-Atellagent-Onboarding-Signature"] + "=="), material)
        nonces.append(nonce)
        return httpx.Response(200, json=responses.pop(0))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        kwargs = dict(platform_origin=args["platform_origin"], origin=args["trust"].service_account_api_origin,
            device=device, setup_id=setup, assignment=assignment, csr_pem=args["csr_pem"], trust=args["trust"])
        assert await fetch_native_certificate(http, **kwargs) is None
        credential = await fetch_native_certificate(http, **kwargs)
        assert credential.service_account_id == assignment.service_account_id
        for field, value in (("setup_id", uuid4()), ("platform_origin", "https://foreign.example"),
                             ("origin", "https://foreign.example:8443")):
            with pytest.raises(OnboardingClientError):
                await fetch_native_certificate(http, **{**kwargs, field: value})
    assert len(set(nonces)) == 2
