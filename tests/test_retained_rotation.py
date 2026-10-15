"""Startup reconciles real retained keys/CA material with one server receipt."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest

from atellagent_client.onboarding.retained_rotation import reconcile_retained_rotation
from atellagent_client.onboarding.renewed_credential import load_renewed_credential
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.connected import rotation_recovery
from atellagent_client.connected.contracts import ConnectedProtocolError
from connected_runtime_support import _config
from atellagent_client.onboarding.service_account_csr import service_account_csr_facts
from atellagent_client.connected.capability import certificate_public_key_sha256
from test_renewed_credential import renewed_case


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["committed", "denied", "mismatch", "outage"])
async def test_startup_retains_identity_and_selects_only_exact_committed_candidate(tmp_path, monkeypatch, state):
    case, initial, staged = renewed_case(tmp_path)
    import json
    record = json.loads(staged.candidate_pointer.read_bytes())
    operation = record["operation_id"]
    calls, configs = [], []
    payload = dict(operation_id=str(uuid4()) if state == "mismatch" else operation,
        status="activated", certificate_public_key_sha256=staged.public_key_sha256,
        certificate_expires_at=staged.certificate_expires_at.isoformat(),
        certificate_rotation_due_at=staged.certificate_expires_at.isoformat(),
        certificate_rotation_deadline_at=staged.certificate_expires_at.isoformat())
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def request_authenticated(self, method, url, *, headers=None):
            assert method == "GET" and url.endswith(f"/{operation}/activation")
            calls.append(url)
            if state == "outage": raise httpx.ReadError("temporarily unavailable")
            return SimpleNamespace(status_code=403 if state == "denied" else 200, json=lambda: payload)
    def session(config, *, device_proof_provider=None):
        assert device_proof_provider is None
        return Session()
    monkeypatch.setattr(rotation_recovery.GatewaySession, "from_service_account_config", session)
    async def make_config(candidate):
        configs.append(candidate)
        return replace(_config(), tenant_id=str(case.args["tenant_id"]),
            service_account_id=str(initial.credential.service_account_id), integration_id=str(initial.credential.integration_id),
            platform_origin=case.args["platform_origin"], cert_path=str(candidate.certificate_path), key_path=str(candidate.private_key_path))
    args = dict(trust=case.args["trust"], tenant_id=case.args["tenant_id"])
    if state == "committed":
        recovered = await reconcile_retained_rotation(initial, **args, make_config=make_config)
        assert recovered == load_renewed_credential(initial, **args)
        assert recovered.certificate_path == staged.staged_certificate_path
        assert recovered.credential.operation_id == initial.credential.operation_id
    else:
        with pytest.raises((OnboardingClientError, ConnectedProtocolError, httpx.ReadError)):
            await reconcile_retained_rotation(initial, **args, make_config=make_config)
        assert not (initial.certificate_path.parent / "renewed.json").exists()
    assert len(calls) == 1 and len(configs) == 1
    assert staged.candidate_pointer.exists() and staged.staged_private_key_path.exists()
    assert len(list(tmp_path.glob("integration-*"))) == 1
    assert initial.certificate_path.exists() and initial.private_key_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_state", ["issued", "wrong-key", "cancelled"])
async def test_unactivated_restart_requires_current_parent_and_exact_pending_operation(tmp_path, monkeypatch, pending_state):
    case, initial, staged = renewed_case(tmp_path, expired_initial=False)
    import json
    operation = json.loads(staged.candidate_pointer.read_bytes())["operation_id"]
    calls = []
    class Session:
        def __init__(self, config, *, device_proof_provider=None):
            assert device_proof_provider is None
            self.config = config
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def request_authenticated(self, method, url, *, headers=None):
            calls.append((method, url))
            if url.endswith("/activation"):
                assert self.config.cert_path == str(staged.staged_certificate_path)
                return SimpleNamespace(status_code=403)
            assert url.endswith("/pending") and self.config.cert_path == str(initial.certificate_path)
            payload = dict(operation_id=operation, status="cancelled" if pending_state == "cancelled" else "issued",
                csr_sha256=service_account_csr_facts(staged.prepared.csr_pem).csr_sha256,
                public_key_sha256="f" * 64 if pending_state == "wrong-key" else staged.public_key_sha256,
                authorization_certificate_public_key_sha256=certificate_public_key_sha256(self.config.cert_path))
            return SimpleNamespace(status_code=200, json=lambda: payload)
    monkeypatch.setattr(rotation_recovery.GatewaySession, "from_service_account_config", Session)
    async def make_config(stored):
        return replace(_config(), tenant_id=str(case.args["tenant_id"]),
            service_account_id=str(initial.credential.service_account_id), integration_id=str(initial.credential.integration_id),
            platform_origin=case.args["platform_origin"], cert_path=str(stored.certificate_path), key_path=str(stored.private_key_path))
    args = dict(trust=case.args["trust"], tenant_id=case.args["tenant_id"], make_config=make_config)
    if pending_state == "issued":
        assert await reconcile_retained_rotation(initial, **args) == initial
    else:
        with pytest.raises(ConnectedProtocolError):
            await reconcile_retained_rotation(initial, **args)
    assert not (tmp_path / "renewed.json").exists()
    assert len(calls) == 2 and all(method == "GET" for method, _ in calls)
