"""Setup resume preserves one immutable request/key bundle and rejects unsafe state."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from uuid import uuid4

import pytest

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.setup_draft import SetupDraft
from atellagent_client.onboarding.setup_keys import SetupKeys
from atellagent_client.onboarding.setup_store import SetupStore


def draft():
    return SetupDraft(uuid4(), uuid4(), uuid4(), "https://platform.example", "device",
        "My Mac", "0.9.1", "macos", SetupKeys.generate())


def load(store, item):
    return store.load(item.operation_id, expected_origin=item.platform_origin,
        expected_tenant_id=item.tenant_id, expected_owner_user_id=item.owner_user_id)


def test_reopen_preserves_exact_intent_and_both_keys_without_generating_keys(tmp_path, monkeypatch):
    item, store = draft(), SetupStore(tmp_path / "setups")
    store.save_initial(item)
    def forbidden():
        raise AssertionError("resume must not generate a key")
    monkeypatch.setattr(SetupKeys, "generate", forbidden)
    restored = load(SetupStore(tmp_path / "setups"), item)
    assert restored.encode() == item.encode() and restored.proposal() == item.proposal()
    assert "PRIVATE KEY" not in repr(restored)


def test_concurrent_creation_does_not_overwrite_winner(tmp_path):
    item, store = draft(), SetupStore(tmp_path / "setups")
    other = replace(item, keys=SetupKeys.generate())
    def save(candidate):
        try:
            store.save_initial(candidate)
            return candidate
        except OnboardingClientError as exc:
            assert exc.code == "onboarding_setup_already_saved"
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [item, other]))
    winners = [candidate for candidate in results if candidate is not None]
    assert len(winners) == 1 and load(store, item).encode() == winners[0].encode()
    assert list(store.directory.glob(".setup-*")) == []


def test_failed_commit_has_no_partial_record(tmp_path, monkeypatch):
    item, store = draft(), SetupStore(tmp_path / "setups")
    def fail(*args, **kwargs):
        raise OSError("simulated commit failure")
    monkeypatch.setattr("atellagent_client.onboarding.atomic_private_file.os.link", fail)
    with pytest.raises(OnboardingClientError, match="storage_unavailable"):
        store.save_initial(item)
    assert list(store.directory.iterdir()) == []


@pytest.mark.parametrize("field", ["tenant_id", "owner_user_id"])
def test_resume_cannot_relabel_owner_or_tenant(tmp_path, field):
    item, store = draft(), SetupStore(tmp_path / "setups")
    store.save_initial(item)
    with pytest.raises(OnboardingClientError, match="storage_unavailable"):
        load(store, replace(item, **{field: uuid4()}))


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission/link cases")
@pytest.mark.parametrize("unsafe", ["symlink", "public_directory", "public_record", "parent_symlink"])
def test_unsafe_paths_are_not_repaired(tmp_path, unsafe):
    item, store = draft(), SetupStore(tmp_path / "setups")
    store.save_initial(item)
    record = store.directory / f"{item.operation_id}.json"
    if unsafe == "symlink":
        target = store.directory / "other.json"
        record.rename(target)
        record.symlink_to(target)
    elif unsafe == "public_directory":
        store.directory.chmod(0o755)
    elif unsafe == "public_record":
        record.chmod(0o644)
    else:
        link = tmp_path / "linked"
        link.symlink_to(store.directory, target_is_directory=True)
        with pytest.raises(OnboardingClientError, match="storage_unavailable"):
            SetupStore(link / "child")
        return
    with pytest.raises(OnboardingClientError, match="storage_unavailable"):
        load(store, item)
    if unsafe == "public_record":
        assert record.stat().st_mode & 0o077 == 0o044


def test_truncated_or_oversized_record_is_not_adopted(tmp_path):
    item, store = draft(), SetupStore(tmp_path / "setups")
    store.save_initial(item)
    path = store.directory / f"{item.operation_id}.json"
    for raw in (b"{", b"x" * 32769):
        path.write_bytes(raw)
        with pytest.raises(OnboardingClientError, match="storage_unavailable"):
            load(store, item)
