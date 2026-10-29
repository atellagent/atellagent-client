"""Recovery lists original keys, never invents approval after transport failure."""

from dataclasses import replace
import json
import os
from types import SimpleNamespace
from uuid import UUID, uuid4
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from atellagent_client.onboarding import native_saved_inventory as inventory
from atellagent_client.onboarding.errors import OnboardingClientError
from native_credential_support import stored_case


def context(case, lookup):
    return SimpleNamespace(drafts=case.draft_store, client=SimpleNamespace(assignment=lookup),
        load=lambda identifier: case.draft_store.load(identifier, device=case.draft,
            setup_id=case.draft.setup_id, expected_origin=case.draft.platform_origin))


@pytest.mark.asyncio
@pytest.mark.parametrize("submitted", [True, False])
async def test_reopening_recovers_original_draft_without_key_generation(tmp_path, monkeypatch, submitted):
    case = stored_case(tmp_path)
    before = case.draft.encode()
    async def lookup(identifier):
        assert identifier == case.draft.proposal.id
        return replace(case.assignment, status="approved", owner_consented_at=None) if submitted else None
    from cryptography.hazmat.primitives.asymmetric import rsa
    monkeypatch.setattr(rsa, "generate_private_key", lambda **kwargs: pytest.fail("inventory regenerated a key"))
    result = await inventory.page(context(case, lookup))
    row = result["requests"][0]
    assert row["request_id"] == str(case.draft.proposal.id)
    assert row["server_status"] == ("approved" if submitted else None)
    assert row["owner_consented_at"] is None and result["next_after"] is None
    assert case.draft_store.require_saved(case.draft).encode() == before
    assert "PRIVATE KEY" not in json.dumps(result) and "tls_public_key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_authority_failure_is_not_not_submitted(tmp_path):
    case = stored_case(tmp_path)
    async def lookup(_): raise OnboardingClientError("device_revoked")
    with pytest.raises(OnboardingClientError, match="device_revoked"):
        await inventory.page(context(case, lookup))


def test_exact_bounded_pagination_uses_retained_records(tmp_path):
    case = stored_case(tmp_path)
    ids = [case.draft.proposal.id]
    for value in range(1, 10):
        identifier = UUID(int=value)
        case.draft_store.save_initial(replace(case.draft, proposal=replace(case.draft.proposal, id=identifier)))
        ids.append(identifier)
    first, cursor = inventory.identifiers(case.draft_store)
    second, end = inventory.identifiers(case.draft_store, after=cursor)
    assert len(first) == 8 and cursor == first[-1]
    assert first + second == sorted(ids) and end is None


@pytest.mark.asyncio
async def test_changed_server_intent_is_not_shown_as_approved(tmp_path):
    case = stored_case(tmp_path)
    changed = replace(case.assignment, onboarding_session_id=uuid4())
    async def lookup(_): return changed
    with pytest.raises(OnboardingClientError, match="onboarding_native_setup_intent_changed"):
        await inventory.page(context(case, lookup))


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture; Windows reparse/ACL acceptance is separate")
async def test_linked_private_draft_fails_without_repair(tmp_path):
    case = stored_case(tmp_path)
    path = case.draft_store._path(case.draft.proposal.id)
    original = path.with_name(".setup-original")
    path.rename(original)
    path.symlink_to(original)
    async def no_lookup(_): pytest.fail("unsafe draft reached server")
    with pytest.raises(OnboardingClientError):
        await inventory.page(context(case, no_lookup))
    assert path.is_symlink()


@pytest.mark.asyncio
async def test_saved_cli_dispatch_is_structured_read_only(tmp_path, monkeypatch, capsys):
    from atellagent_client.cli import setup_native, setup_native_saved
    case = stored_case(tmp_path)
    async def lookup(_): return None
    @asynccontextmanager
    async def opened(): yield context(case, lookup)
    monkeypatch.setattr(setup_native_saved.NativeSetupContext, "open", AsyncMock(return_value=opened()))
    before = case.draft.encode()
    await setup_native.run(["saved", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["requests"][0]["request_id"] == str(case.draft.proposal.id)
    assert result["requests"][0]["server_status"] is None
    assert case.draft_store.require_saved(case.draft).encode() == before


@pytest.mark.asyncio
async def test_invalid_cursor_does_not_open_device(monkeypatch):
    from atellagent_client.cli import setup_native_saved
    async def no_open(*_): pytest.fail("invalid cursor opened device")
    monkeypatch.setattr(setup_native_saved.NativeSetupContext, "open", no_open)
    with pytest.raises(SystemExit) as stopped:
        await setup_native_saved.run(["--json", "--after", "not-a-request"])
    assert stopped.value.code == 1
