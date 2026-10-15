"""Credential generations become usable together and resume without key repair."""

import json
from uuid import uuid4

import pytest

from atellagent_client.onboarding.device_certificate_verify import verify_device_delivery
from atellagent_client.onboarding.device_credential_store import DeviceCredentialStore
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.setup_store import SetupStore
from atellagent_client.onboarding.atomic_private_file import create_private_file
from device_certificate_support import certificate_case


def case(tmp_path):
    draft, receipt, trust, payload = certificate_case()
    setups, credentials = SetupStore(tmp_path / "setups"), DeviceCredentialStore(tmp_path / "credentials")
    setups.save_initial(draft)
    verified = verify_device_delivery(draft=draft, receipt=receipt, trust=trust, payload=payload)
    return draft, receipt, trust, verified, setups, credentials


def save(data):
    draft, receipt, trust, credential, setups, store = data
    return store.save(draft=draft, receipt=receipt, trust=trust, credential=credential, setup_store=setups)


def load(data):
    draft, receipt, trust, credential, _, store = data
    return store.load(draft=draft, receipt=receipt, trust=trust, fingerprint=credential.certificate_sha256)


def test_complete_commit_and_repeat_have_one_immutable_generation(tmp_path):
    data = case(tmp_path)
    first, second = save(data), save(data)
    assert first == second == load(data)
    assert first.certificate_path.exists() and first.private_key_path.exists()
    assert len(list(data[-1].directory.glob("device-*"))) == 1
    assert len(list(data[-1].directory.glob("*.json"))) == 1


def test_failed_key_write_leaves_no_commit_or_partial_generation(tmp_path, monkeypatch):
    data = case(tmp_path)
    from atellagent_client.onboarding import credential_generation
    original = credential_generation.create_private_file
    def write(path, content):
        if path.name == "private-key.pem":
            raise OSError("simulated key write failure")
        original(path, content)
    monkeypatch.setattr(credential_generation, "create_private_file", write)
    with pytest.raises(OnboardingClientError, match="storage_unavailable"):
        save(data)
    assert list(data[-1].directory.iterdir()) == []


def test_failure_after_pointer_link_preserves_complete_generation_for_resume(tmp_path, monkeypatch):
    data = case(tmp_path)
    from atellagent_client.onboarding import atomic_private_file
    original = atomic_private_file.sync_directory
    def sync(path):
        if path == data[-1].directory:
            raise OSError("simulated pointer fsync failure")
        original(path)
    monkeypatch.setattr(atomic_private_file, "sync_directory", sync)
    with pytest.raises(OnboardingClientError, match="storage_unavailable"):
        save(data)
    restored = load(data)
    assert restored.private_key_path.exists() and restored.certificate_path.exists()
    assert len(list(data[-1].directory.glob("device-*"))) == 1


@pytest.mark.parametrize("change", ["owner", "generation", "key", "certificate"])
def test_changed_committed_state_is_not_adopted_or_repaired(tmp_path, change):
    data = case(tmp_path)
    saved = save(data)
    pointer = next(data[-1].directory.glob("*.json"))
    record = json.loads(pointer.read_bytes())
    if change == "owner":
        record["owner_user_id"] = str(uuid4())
        pointer.write_text(json.dumps(record))
    elif change == "generation":
        record["generation"] = "../outside"
        pointer.write_text(json.dumps(record))
    elif change == "key":
        saved.private_key_path.write_bytes(b"wrong-key")
    else:
        saved.certificate_path.write_bytes(b"wrong-certificate")
    with pytest.raises(OnboardingClientError):
        load(data)


def test_valid_generation_cannot_be_selected_under_another_fingerprint(tmp_path):
    data = case(tmp_path)
    save(data)
    draft, receipt, trust, _, _, store = data
    pointer = next(store.directory.glob("*.json"))
    alternate = "b" * 64
    create_private_file(store.directory / f"{draft.operation_id}-{alternate}.json", pointer.read_bytes())
    with pytest.raises(OnboardingClientError):
        store.load(draft=draft, receipt=receipt, trust=trust, fingerprint=alternate)


def test_expired_original_can_be_read_only_as_verified_activation_history(tmp_path):
    from datetime import timedelta
    from cryptography import x509
    from atellagent_client.onboarding.credential_generation import commit_credential_generation
    from atellagent_client.onboarding.setup_key_encoding import encode_keys
    draft, receipt, trust, payload = certificate_case(expired=True)
    leaf = x509.load_pem_x509_certificate(payload["certificate_pem"].encode())
    verified = verify_device_delivery(draft=draft, receipt=receipt, trust=trust, payload=payload,
        verification_time=leaf.not_valid_before_utc + timedelta(seconds=1))
    store = DeviceCredentialStore(tmp_path / "credentials")
    commit_credential_generation(store.directory, store._pointer(draft, verified.certificate_sha256),
        kind="device", metadata=store._metadata(draft, verified),
        certificate=(payload["certificate_pem"] + payload["certificate_chain_pem"]).encode(),
        private_key=encode_keys(draft.keys)["tls_key_pem"].encode())
    arguments = dict(draft=draft, receipt=receipt, trust=trust, fingerprint=verified.certificate_sha256)
    assert store.load_history(**arguments).credential.certificate_sha256 == verified.certificate_sha256
    with pytest.raises(OnboardingClientError):
        store.load(**arguments)
