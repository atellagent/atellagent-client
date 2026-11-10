"""Native retries retain the original separate key and exact requested boundary."""

from dataclasses import replace
import json
import os
from types import SimpleNamespace
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric import rsa
import pytest

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_setup_draft import NativeSetupDraft
from atellagent_client.onboarding.native_setup_store import NativeSetupStore
from atellagent_client.onboarding.service_account_csr import service_account_csr


@pytest.fixture
def draft():
    device = SimpleNamespace(tenant_id=uuid4(), device_id=uuid4(), owner_user_id=uuid4())
    return NativeSetupDraft.create(device=device, setup_id=uuid4(), request_id=uuid4(), platform_origin="https://platform.example",
        display_name="My host", packaging="sdk", native_host="codex", enforcement_mode="enforce",
        coverage_posture="strict")


def test_saved_retry_retains_key_and_proposal_without_generation(tmp_path, draft, monkeypatch):
    store = NativeSetupStore(tmp_path / "native")
    store.save_initial(draft)
    monkeypatch.setattr(rsa, "generate_private_key", lambda **kwargs: pytest.fail("resume generated a new key"))
    saved = store.require_saved(draft)
    assert saved.encode() == draft.encode()
    account = uuid4()
    assert service_account_csr(saved.tls_key, tenant_id=saved.tenant_id, service_account_id=account) == (
        service_account_csr(draft.tls_key, tenant_id=draft.tenant_id, service_account_id=account))
    assert "PRIVATE KEY" not in repr(saved)
    if os.name != "nt":
        assert (store.directory / f"{draft.proposal.id}.json").stat().st_mode & 0o777 == 0o600
        assert store.directory.stat().st_mode & 0o777 == 0o700


def test_duplicate_draft_never_overwrites_saved_state(tmp_path, draft):
    store = NativeSetupStore(tmp_path / "native")
    store.save_initial(draft)
    original = store.require_saved(draft).encode()
    with pytest.raises(OnboardingClientError, match="already_saved"):
        store.save_initial(replace(draft, proposal=replace(draft.proposal, display_name="Changed intent")))
    assert store.require_saved(draft).encode() == original


@pytest.mark.parametrize("field", ["tenant_id", "device_id", "owner_user_id", "setup_id", "platform_origin"])
def test_wrong_installation_cannot_reload(tmp_path, draft, field):
    store = NativeSetupStore(tmp_path / "native")
    store.save_initial(draft)
    changed = replace(draft, **{field: "https://foreign.example" if field == "platform_origin" else uuid4()})
    with pytest.raises(OnboardingClientError):
        store.load(draft.proposal.id, device=changed, setup_id=changed.setup_id,
                   expected_origin=changed.platform_origin)


@pytest.mark.parametrize("change", ["extra", "revision", "key", "proposal"])
def test_closed_draft_rejects_changed_key_or_protocol(draft, change):
    payload = json.loads(draft.encode())
    if change == "extra":
        payload["enrollment_token"] = "obsolete"
    elif change == "revision":
        payload["revision"] = True
    elif change == "key":
        payload["tls_key_pem"] = "not a key"
    else:
        payload["proposal"]["tls_public_key_der_base64"] = "not a public key"
    with pytest.raises(ValueError):
        NativeSetupDraft.decode(json.dumps(payload))


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions; Windows uses ACL validation")
@pytest.mark.parametrize("unsafe", ["permissions", "symlink"])
def test_unsafe_existing_draft_is_not_repaired(tmp_path, draft, unsafe):
    store = NativeSetupStore(tmp_path / "native")
    store.save_initial(draft)
    path = store.directory / f"{draft.proposal.id}.json"
    if unsafe == "permissions":
        path.chmod(0o644)
    else:
        target = store.directory / "original.json"
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises(OnboardingClientError):
        store.require_saved(draft)
    if unsafe == "permissions":
        assert path.stat().st_mode & 0o777 == 0o644
    else:
        assert path.is_symlink()
