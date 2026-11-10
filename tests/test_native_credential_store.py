"""Native generation commits never replace retained keys or admit partial pairs."""

from dataclasses import replace
import json
from uuid import uuid4

import pytest

from atellagent_client.onboarding import credential_generation
from atellagent_client.onboarding.atomic_private_file import create_private_file
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_credential_store import NativeCredentialStore
from native_credential_support import stored_case


@pytest.fixture
def saved(tmp_path):
    case = stored_case(tmp_path)
    store = NativeCredentialStore(tmp_path / "credentials")
    kwargs = {field: getattr(case, field) for field in ("draft", "assignment", "trust", "credential", "draft_store")}
    return case, store, kwargs


def test_complete_commit_and_retry_reuse_exact_generation(saved):
    case, store, kwargs = saved
    first = store.save(**kwargs)
    second = store.save(**kwargs)
    assert first == second
    assert first.private_key_path.read_text() == case.draft.private_key_pem()
    assert first.credential.service_account_id == case.assignment.service_account_id
    assert len(list(store.directory.glob("integration-*"))) == 1
    assert not hasattr(first, "activation_receipt")


def test_interrupted_pair_does_not_commit_pointer_and_retry_recovers(saved, monkeypatch):
    _, store, kwargs = saved
    original = credential_generation.create_private_file
    def fail_key(path, content):
        if path.name == "private-key.pem":
            raise OSError("interrupted")
        return original(path, content)
    monkeypatch.setattr(credential_generation, "create_private_file", fail_key)
    with pytest.raises(OnboardingClientError):
        store.save(**kwargs)
    assert list(store.directory.iterdir()) == []
    monkeypatch.setattr(credential_generation, "create_private_file", original)
    assert store.save(**kwargs).certificate_path.exists()


@pytest.mark.parametrize("change", ["owner", "intent", "certificate"])
def test_changed_scope_or_material_not_committed(saved, change):
    case, store, kwargs = saved
    if change == "owner":
        kwargs["draft"] = replace(case.draft, owner_user_id=uuid4())
    elif change == "intent":
        kwargs["assignment"] = replace(case.assignment, intent_sha256="a" * 64)
    else:
        kwargs["credential"] = replace(case.credential, certificate_sha256="a" * 64)
    with pytest.raises(OnboardingClientError):
        store.save(**kwargs)
    assert list(store.directory.iterdir()) == []


@pytest.mark.parametrize("change", ["traversal", "extra", "key"])
def test_committed_state_is_revalidated_not_repaired(saved, change):
    case, store, kwargs = saved
    record = store.save(**kwargs)
    pointer = next(store.directory.glob("*.json"))
    if change == "key":
        record.private_key_path.write_text("not a private key")
    else:
        data = json.loads(pointer.read_text())
        data.update({"generation": "../outside"} if change == "traversal" else {"legacy": True})
        pointer.write_text(json.dumps(data))
    with pytest.raises(OnboardingClientError):
        store.load(draft=case.draft, assignment=case.assignment, trust=case.trust,
                   fingerprint=case.credential.certificate_sha256, draft_store=case.draft_store)


def test_valid_generation_cannot_be_selected_under_another_fingerprint(saved):
    case, store, kwargs = saved
    store.save(**kwargs)
    pointer = next(store.directory.glob("*.json"))
    alternate = "b" * 64
    create_private_file(store.directory / f"{case.draft.proposal.id}-{alternate}.json", pointer.read_bytes())
    with pytest.raises(OnboardingClientError):
        store.load(draft=case.draft, assignment=case.assignment, trust=case.trust,
                   fingerprint=alternate, draft_store=case.draft_store)
