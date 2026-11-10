"""Lost activation replies use the retained new identity for a read-only receipt."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest

from atellagent_client.onboarding.atomic_private_file import create_private_file
from atellagent_client.onboarding.proofs import canonical_json
from atellagent_client.connected import rotation_recovery
from atellagent_client.connected.contracts import ConnectedProtocolError
from connected_runtime_support import _config


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "operation", "key", "expiry", "status", "denied"])
async def test_exact_current_receipt_with_new_pair_and_no_activation_replay(tmp_path, monkeypatch, change):
    operation = uuid4()
    staged = SimpleNamespace(candidate_pointer=tmp_path / "candidate.json",
        staged_certificate_path=tmp_path / "replacement.pem", staged_private_key_path=tmp_path / "replacement-key.pem",
        public_key_sha256="b" * 64, certificate_expires_at=datetime.now(timezone.utc) + timedelta(days=30))
    create_private_file(staged.candidate_pointer, canonical_json({"operation_id": str(operation)}))
    payload = dict(operation_id=str(operation), status="activated", certificate_public_key_sha256="b" * 64,
        certificate_expires_at=staged.certificate_expires_at.isoformat(),
        certificate_rotation_due_at=(staged.certificate_expires_at - timedelta(days=7)).isoformat(),
        certificate_rotation_deadline_at=staged.certificate_expires_at.isoformat())
    if change == "operation": payload["operation_id"] = str(uuid4())
    if change == "key": payload["certificate_public_key_sha256"] = "f" * 64
    if change == "expiry": payload["certificate_expires_at"] = datetime.now(timezone.utc).isoformat()
    if change == "status": payload["status"] = "issued"
    calls, selected, closed = [], [], []
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): closed.append(True)
        async def request_authenticated(self, method, url):
            calls.append((method, url, proof(method, url, None)))
            return SimpleNamespace(status_code=403 if change == "denied" else 200, json=lambda: payload)
    def session(config, *, device_proof_provider):
        assert device_proof_provider is proof
        selected.append(config)
        return Session()
    monkeypatch.setattr(rotation_recovery.GatewaySession, "from_service_account_config", session)
    config = _config()
    def proof(method, url, body):
        assert method == "GET" and body is None
        return {"device-proof": "fresh"}
    if change is None:
        result = await rotation_recovery.recover_activated_rotation(config=config, staged=staged, device_proof_provider=proof)
        assert result == payload
    else:
        with pytest.raises(ConnectedProtocolError):
            await rotation_recovery.recover_activated_rotation(config=config, staged=staged, device_proof_provider=proof)
    assert closed == [True]
    assert selected[0].cert_path == str(staged.staged_certificate_path)
    assert selected[0].key_path == str(staged.staged_private_key_path)
    assert config.cert_path != selected[0].cert_path
    assert calls == [("GET", config.gateway_url.rstrip("/") +
        f"/v1/connected-runtimes/certificate-rotations/{operation}/activation", {"device-proof": "fresh"})]
