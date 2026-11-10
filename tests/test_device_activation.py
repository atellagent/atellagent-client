"""Activation uses committed device mTLS material and a setup-bound signature."""

import base64
from hashlib import sha256
import json
import ssl

import httpx
import pytest

from atellagent_client.onboarding.device_activation import activate_stored_device
from atellagent_client.onboarding.device_certificate_verify import verify_device_delivery
from atellagent_client.onboarding.device_credential_store import DeviceCredentialStore
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.setup_store import SetupStore
from device_certificate_support import certificate_case


def fixture(tmp_path, monkeypatch, responses):
    draft, receipt, trust, payload = certificate_case()
    setups, credentials = SetupStore(tmp_path / "setups"), DeviceCredentialStore(tmp_path / "credentials")
    setups.save_initial(draft)
    verified = verify_device_delivery(draft=draft, receipt=receipt, trust=trust, payload=payload)
    credentials.save(draft=draft, receipt=receipt, trust=trust, credential=verified, setup_store=setups)
    nonces = []
    factory = httpx.AsyncClient
    async def respond(request):
        assert str(request.url) == trust.device_api_origin + f"/api/client-onboarding/sessions/{draft.operation_id}/activate"
        assert "authorization" not in request.headers and "X-Atellagent-Device-Signature" not in request.headers
        body = json.loads(request.content)
        assert body["certificate_sha256"] == verified.certificate_sha256
        timestamp = request.headers["X-Atellagent-Onboarding-Timestamp"]
        nonce = request.headers["X-Atellagent-Onboarding-Nonce"]
        material = "\n".join(("atellagent-onboarding-request-v1", str(draft.operation_id), "POST", request.url.path,
            sha256(request.content).hexdigest(), timestamp, nonce)).encode()
        signature = base64.urlsafe_b64decode(request.headers["X-Atellagent-Onboarding-Signature"] + "==")
        draft.keys.proof_key.public_key().verify(signature, material)
        nonces.append(nonce)
        result = responses.pop(0)
        if result == "timeout":
            raise httpx.ReadTimeout("simulated lost activation response")
        return httpx.Response(200, json={"device_id": str(receipt.device_id), "status": "device_ready",
            "reporting_enabled": result})
    def create(**kwargs):
        context = kwargs["verify"]
        assert isinstance(context, ssl.SSLContext) and context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return factory(**kwargs, transport=httpx.MockTransport(respond))
    monkeypatch.setattr("atellagent_client.onboarding.device_http.httpx.AsyncClient", create)
    arguments = dict(draft=draft, receipt=receipt, trust=trust, setup_store=setups,
        credential_store=credentials, fingerprint=verified.certificate_sha256, reporting_enabled=True)
    return arguments, nonces


@pytest.mark.asyncio
async def test_lost_response_retry_keeps_credentials_and_returns_current_opt_out(tmp_path, monkeypatch):
    arguments, nonces = fixture(tmp_path, monkeypatch, ["timeout", False])
    with pytest.raises(OnboardingClientError) as raised:
        await activate_stored_device(**arguments)
    assert raised.value.retryable is True
    assert list(arguments["credential_store"].directory.glob("device-*/activation.json")) == []
    active = await activate_stored_device(**arguments)
    assert active.reporting_enabled is False
    assert active.device.device_id == arguments["receipt"].device_id
    assert active.device.tenant_id == arguments["draft"].tenant_id
    assert nonces[0] != nonces[1]
    assert len(list(arguments["credential_store"].directory.glob("device-*"))) == 1
    assert len(list(arguments["credential_store"].directory.glob("device-*/activation.json"))) == 1


@pytest.mark.asyncio
async def test_untyped_reporting_receipt_does_not_promote_device(tmp_path, monkeypatch):
    arguments, _ = fixture(tmp_path, monkeypatch, [1])
    with pytest.raises(OnboardingClientError, match="activation_response_invalid"):
        await activate_stored_device(**arguments)


@pytest.mark.asyncio
async def test_receipt_storage_failure_requires_recovery_before_returning_ready(tmp_path, monkeypatch):
    arguments, nonces = fixture(tmp_path, monkeypatch, [True, False])
    from atellagent_client.onboarding import device_activation_receipt
    original = device_activation_receipt.create_private_file
    failed = False
    def write(path, content):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated activation receipt write failure")
        original(path, content)
    monkeypatch.setattr(device_activation_receipt, "create_private_file", write)
    with pytest.raises(OnboardingClientError, match="activation_receipt_unavailable"):
        await activate_stored_device(**arguments)
    assert list(arguments["credential_store"].directory.glob("device-*/activation.json")) == []
    restored = await activate_stored_device(**arguments)
    assert restored.reporting_enabled is False and nonces[0] != nonces[1]
