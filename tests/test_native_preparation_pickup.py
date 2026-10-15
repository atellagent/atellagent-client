"""Pickup retains the original private key across failed responses and restarts."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from atellagent_client.onboarding.device_preparations import DevicePreparationPage
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_preparation_draft import preparation_draft
from atellagent_client.onboarding.native_preparation_pickup import pickup_preparation
from atellagent_client.onboarding.native_preparation_receipt import pickup_intent_digest
from atellagent_client.onboarding.native_setup_store import NativeSetupStore
from test_device_preparations import preparation


@pytest.fixture
def context(tmp_path):
    value = preparation()
    prepared = DevicePreparationPage.from_payload(dict(preparations=[value], next_after=None)).preparations[0]
    device = SimpleNamespace(tenant_id=uuid4(), owner_user_id=uuid4(), device_id=uuid4())
    setup = SimpleNamespace(operation_id=uuid4(), platform_origin="https://platform.example")
    client = SimpleNamespace(device=device, preparations=AsyncMock(return_value=SimpleNamespace(preparations=(prepared,))),
        _request=AsyncMock(), assignment=AsyncMock())
    return SimpleNamespace(client=client, setup=setup, drafts=NativeSetupStore(tmp_path / "drafts")), prepared


def test_draft_is_durable_exact_and_reuses_same_private_key(context):
    ctx, prepared = context
    draft = preparation_draft(ctx, prepared)
    assert draft.proposal.id == prepared.assignment_id
    assert preparation_draft(ctx, prepared).encode() == draft.encode()
    assert ctx.drafts.load_if_present(prepared.assignment_id, device=ctx.client.device,
        setup_id=ctx.setup.operation_id, expected_origin=ctx.setup.platform_origin).encode() == draft.encode()


def test_server_proposal_without_local_key_cannot_rotate_or_reenroll(context):
    ctx, prepared = context
    prepared = replace(prepared, status="proposed", proposed_request_id=prepared.assignment_id)
    with pytest.raises(OnboardingClientError) as error:
        preparation_draft(ctx, prepared)
    assert error.value.code == "onboarding_native_preparation_key_unavailable"


def test_changed_prepared_settings_do_not_replace_retained_key(context):
    ctx, prepared = context
    original = preparation_draft(ctx, prepared)
    value = preparation(prepared.assignment_id)
    value["settings"]["display_name"] = "Different"
    from hashlib import sha256
    from atellagent_client.onboarding.proofs import canonical_json
    value["settings_sha256"] = sha256(canonical_json(value["settings"])).hexdigest()
    changed = DevicePreparationPage.from_payload(dict(preparations=[value], next_after=None)).preparations[0]
    with pytest.raises(OnboardingClientError):
        preparation_draft(ctx, changed)
    assert ctx.drafts.require_saved(original).encode() == original.encode()


@pytest.mark.asyncio
async def test_interrupted_pickup_response_retries_exact_saved_key(context):
    ctx, prepared = context
    ctx.client._request.side_effect = OnboardingClientError("device_transport_unavailable", retryable=True)
    with pytest.raises(OnboardingClientError):
        await pickup_preparation(ctx, prepared.assignment_id)
    saved = preparation_draft(ctx, prepared)
    submitted = ctx.client._request.await_args.args
    assert submitted == ("POST", f"/devices/self/preparations/{prepared.assignment_id}/propose",
        {"proposal": saved.proposal.payload()})
    receipt = dict(id=str(prepared.assignment_id), revision=1, status="pending",
        intent_sha256="a" * 64, expires_at="2099-10-01T00:00:00+00:00")
    assignment = SimpleNamespace(intent_sha256="a" * 64, proposal=saved.proposal, onboarding_session_id=saved.setup_id)
    ctx.client._request.side_effect = None
    ctx.client._request.return_value = receipt
    ctx.client.assignment.return_value = assignment
    assert await pickup_preparation(ctx, prepared.assignment_id) is assignment
    assert ctx.client._request.await_args.args == submitted
    assert preparation_draft(ctx, prepared).encode() == saved.encode()


@pytest.mark.asyncio
async def test_missing_preparation_never_submits_or_creates_key(context):
    ctx, prepared = context
    ctx.client.preparations.return_value = SimpleNamespace(preparations=())
    with pytest.raises(OnboardingClientError):
        await pickup_preparation(ctx, prepared.assignment_id)
    ctx.client._request.assert_not_awaited()


@pytest.mark.parametrize("field,value", [("id", str(uuid4())), ("revision", True),
    ("status", "authenticated"), ("intent_sha256", "invalid"), ("approved", True)])
def test_receipt_cannot_claim_another_request_or_authority(field, value):
    identifier = uuid4()
    payload = dict(id=str(identifier), revision=1, status="pending", intent_sha256="a" * 64,
        expires_at="2099-10-01T00:00:00+00:00")
    with pytest.raises(OnboardingClientError):
        pickup_intent_digest({**payload, field: value}, request_id=identifier)
