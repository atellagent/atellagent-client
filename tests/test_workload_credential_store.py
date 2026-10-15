"""Durable workload candidates retain exactly the deployment's preapproved key."""

import json
import os
from dataclasses import replace
from uuid import uuid4

import pytest

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.service_account_certificate_verify import verify_service_account_delivery
from atellagent_client.onboarding.workload_credential_store import WorkloadCredentialStore
from atellagent_client.onboarding.workload_request_receipt import WorkloadRequestReceipt
from test_workload_proof_client import case
from test_workload_management import draft


@pytest.fixture
def stored_case(case, tmp_path):
    draft, draft_store, payload, certificate = case
    store = WorkloadCredentialStore(tmp_path / "credentials")
    args = dict(draft=draft, receipt=WorkloadRequestReceipt.from_payload(payload, draft=draft),
                trust=certificate.args["trust"], draft_store=draft_store)
    credential = verify_service_account_delivery(**certificate.args)
    return store, args, credential


def test_saved_generation_reopens_without_desktop_or_login(stored_case):
    store, args, credential = stored_case
    first = store.save(**args, credential=credential)
    reopened = WorkloadCredentialStore(store.directory).load(**args)
    assert reopened == first and reopened.credential == credential
    assert store.save(**args, credential=credential) == first
    assert len(list(store.directory.glob("integration-*"))) == 1
    assert first.certificate_path.is_file() and first.private_key_path.is_file()
    assert "PRIVATE KEY" not in repr(first)
    if os.name != "nt":
        assert first.private_key_path.stat().st_mode & 0o777 == 0o600


def test_delivery_without_trailing_pem_newlines_commits_same_certificate(stored_case):
    store, args, credential = stored_case
    delivered = replace(credential, certificate_pem=credential.certificate_pem.strip(),
                        certificate_chain_pem=credential.certificate_chain_pem.strip())
    saved = store.save(**args, credential=delivered)
    assert saved.credential == credential
    assert store.save(**args, credential=delivered) == saved


@pytest.mark.parametrize("change", ["metadata", "key", "certificate", "symlink"])
def test_mutated_generation_never_becomes_runtime_credentials(stored_case, change, tmp_path):
    store, args, credential = stored_case
    saved = store.save(**args, credential=credential)
    if change == "metadata":
        pointer = store._pointer(args["draft"].proposal.id)
        record = json.loads(pointer.read_bytes())
        record["intent_sha256"] = "f" * 64
        pointer.write_text(json.dumps(record))
    elif change == "key":
        saved.private_key_path.write_text("not the retained key")
    elif change == "certificate":
        saved.certificate_path.write_text("not a certificate")
    else:
        if os.name == "nt":
            pytest.skip("Windows reparse/ACL boundaries have dedicated platform tests")
        saved.private_key_path.unlink()
        other = tmp_path / "unreviewed-key"
        other.write_text("not the retained key")
        saved.private_key_path.symlink_to(other)
    with pytest.raises(OnboardingClientError):
        store.load(**args)


def test_commit_crash_can_recover_without_new_key_or_overwrite(stored_case, monkeypatch):
    store, args, credential = stored_case
    from atellagent_client.onboarding import workload_credential_store as module
    commit = module.commit_credential_generation
    def lost_response(*positional, **kwargs):
        commit(*positional, **kwargs)
        raise OSError("lost return after durable commit")
    monkeypatch.setattr(module, "commit_credential_generation", lost_response)
    with pytest.raises(OnboardingClientError):
        store.save(**args, credential=credential)
    assert WorkloadCredentialStore(store.directory).load(**args).credential == credential


def test_incomplete_commit_cleans_only_its_uncommitted_generation(stored_case, monkeypatch):
    store, args, credential = stored_case
    from atellagent_client.onboarding import credential_generation as module
    create = module.create_private_file
    def fail_pointer(path, content):
        if path.parent == store.directory:
            raise OSError("pointer unavailable")
        create(path, content)
    monkeypatch.setattr(module, "create_private_file", fail_pointer)
    with pytest.raises(OnboardingClientError):
        store.save(**args, credential=credential)
    assert list(store.directory.iterdir()) == []


def test_missing_generation_requires_recovery_not_false_success(stored_case):
    store, args, _ = stored_case
    with pytest.raises(OnboardingClientError, match="credential_recovery_required"):
        store.load(**args)


def test_initial_credential_selection_cannot_be_replaced_on_retry(stored_case):
    store, args, credential = stored_case
    first = store.save(**args, credential=credential)
    with pytest.raises(OnboardingClientError, match="selection_changed"):
        store.save(**args, credential=replace(credential, operation_id=uuid4()))
    assert store.load(**args) == first
    assert len(list(store.directory.glob("integration-*"))) == 1
