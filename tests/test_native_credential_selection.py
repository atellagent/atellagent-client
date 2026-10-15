"""Recovery uses an exact committed candidate, never an arbitrary generation."""

from dataclasses import replace
import json

import pytest

from atellagent_client.onboarding import native_credential_selection
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_credential_store import NativeCredentialStore
from native_credential_support import stored_case


@pytest.fixture
def saved(tmp_path):
    case = stored_case(tmp_path)
    store = NativeCredentialStore(tmp_path / "credentials")
    scope = {name: getattr(case, name) for name in ("draft", "assignment", "trust", "draft_store")}
    store.save(**scope, credential=case.credential)
    return case, store, scope


def test_missing_selection_requires_explicit_recovery_even_with_saved_generation(saved):
    _, store, scope = saved
    with pytest.raises(OnboardingClientError, match="onboarding_native_credential_recovery_required"):
        store.load_selected(**scope)


def test_selection_survives_restart_and_server_installed_status(saved):
    case, store, scope = saved
    first = store.select(**scope, fingerprint=case.credential.certificate_sha256)
    assert store.select(**scope, fingerprint=case.credential.certificate_sha256) == first
    recovered = NativeCredentialStore(store.directory)
    scope["assignment"] = replace(case.assignment, status="installed")
    assert recovered.load_selected(**scope) == first
    assert len(list(store.directory.glob("integration-*"))) == 1


@pytest.mark.parametrize("change", ["intent_sha256", "operation_id", "extra", "revision"])
def test_selected_metadata_must_match_verified_generation(saved, change):
    case, store, scope = saved
    store.select(**scope, fingerprint=case.credential.certificate_sha256)
    pointer = store.directory / f"{case.draft.proposal.id}-selected.json"
    record = json.loads(pointer.read_text())
    record[change] = True
    pointer.write_text(json.dumps(record))
    with pytest.raises(OnboardingClientError):
        store.load_selected(**scope)


def test_selection_write_failure_leaves_recoverable_generation(saved, monkeypatch):
    case, store, scope = saved
    original = native_credential_selection.create_private_file
    def interrupted(*args):
        raise OSError("interrupted")
    monkeypatch.setattr(native_credential_selection, "create_private_file", interrupted)
    with pytest.raises(OnboardingClientError):
        store.select(**scope, fingerprint=case.credential.certificate_sha256)
    monkeypatch.setattr(native_credential_selection, "create_private_file", original)
    assert store.select(**scope, fingerprint=case.credential.certificate_sha256)
