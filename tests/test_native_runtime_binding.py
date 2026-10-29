"""Request signing proves a device, not a cached allow or a human OAuth session."""

import base64
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest

from atellagent_client.onboarding import native_runtime_binding
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_runtime_binding import open_native_runtime_binding
from atellagent_client.onboarding.proofs import canonical_json
from native_runtime_binding_support import runtime_case


def test_runtime_headers_sign_exact_body_query_and_fresh_nonce_without_human_token(tmp_path):
    binding = runtime_case(tmp_path).binding
    url = binding.gateway_origin + "/v1/agents/boundary/actions/preflight?source=native"
    body = {"action": "read", "path": "/tmp/test"}
    first = binding.request_headers("POST", url, body)
    second = binding.request_headers("POST", url, body)
    assert set(first) == {"X-Atellagent-Local-Authorization-Id", "X-Atellagent-Device-Id",
                         "X-Atellagent-Device-Timestamp", "X-Atellagent-Device-Nonce", "X-Atellagent-Device-Signature"}
    assert first["X-Atellagent-Local-Authorization-Id"] == str(binding.authorization_id)
    assert first["X-Atellagent-Device-Nonce"] != second["X-Atellagent-Device-Nonce"]
    material = "\n".join(("atellagent-device-request-v1", str(binding.device.device_id), "POST",
        "/v1/agents/boundary/actions/preflight?source=native", sha256(canonical_json(body)).hexdigest(),
        first["X-Atellagent-Device-Timestamp"], first["X-Atellagent-Device-Nonce"])).encode()
    binding.device.proof_key.public_key().verify(base64.urlsafe_b64decode(
        first["X-Atellagent-Device-Signature"] + "=="), material)


@pytest.mark.parametrize("url", ["https://other.example/v1/action", "http://mtls.example/v1/action",
    "https://user:password@mtls.example/v1/action", "https://mtls.example/v1/action#fragment"])
def test_device_key_is_not_used_for_untrusted_target(tmp_path, url):
    with pytest.raises(OnboardingClientError, match="onboarding_native_request_origin_mismatch"):
        runtime_case(tmp_path).binding.request_headers("POST", url, {})


def test_action_correlation_is_scoped_by_exact_delegation_and_epoch(tmp_path):
    binding = runtime_case(tmp_path).binding
    key = binding.action_key("session:tool")
    assert key == binding.action_key("session:tool")
    assert key != replace(binding, authorization_epoch=2).action_key("session:tool")
    assert key != replace(binding, authorization_id=uuid4()).action_key("session:tool")
    with pytest.raises(OnboardingClientError):
        binding.action_key(" ")
    with pytest.raises(ValueError):
        replace(binding, authorization_epoch=True)
    with pytest.raises(ValueError):
        replace(binding, authorization_epoch=2**31)


@pytest.mark.asyncio
async def test_runtime_binding_requires_current_activation_before_selecting_credentials(tmp_path, monkeypatch):
    item = runtime_case(tmp_path)
    assignment, draft = item.case.assignment, item.case.draft
    calls = []
    async def advance():
        calls.append("current_activation")
        return SimpleNamespace(stage="awaiting_registration", assignment=assignment,
            activation=SimpleNamespace(authorization_id=item.binding.authorization_id, authorization_epoch=1))
    async def trust(origin):
        assert calls == ["current_activation"]
        return item.case.trust
    context = SimpleNamespace(load=lambda identifier: draft, flow=lambda draft: SimpleNamespace(advance=advance),
        credentials=item.store, drafts=item.case.draft_store, client=SimpleNamespace(device=item.binding.device))
    monkeypatch.setattr(native_runtime_binding, "fetch_service_account_trust", trust)
    with pytest.raises(OnboardingClientError, match="onboarding_native_credential_recovery_required"):
        await open_native_runtime_binding(context, assignment.request_id)
    item.store.select(draft=draft, assignment=assignment, trust=item.case.trust,
                      fingerprint=item.case.credential.certificate_sha256, draft_store=item.case.draft_store)
    calls.clear()
    result = await open_native_runtime_binding(context, assignment.request_id)
    assert result == item.binding


@pytest.mark.asyncio
async def test_unactivated_progress_never_opens_runtime_credentials(tmp_path, monkeypatch):
    item = runtime_case(tmp_path)
    async def advance():
        return SimpleNamespace(stage="awaiting_approval", activation=None)
    async def no_trust(*_):
        pytest.fail("unactivated request must not load runtime trust/credentials")
    monkeypatch.setattr(native_runtime_binding, "fetch_service_account_trust", no_trust)
    context = SimpleNamespace(load=lambda _: item.case.draft, flow=lambda _: SimpleNamespace(advance=advance))
    with pytest.raises(OnboardingClientError, match="onboarding_native_runtime_not_activated"):
        await open_native_runtime_binding(context, item.case.assignment.request_id)
