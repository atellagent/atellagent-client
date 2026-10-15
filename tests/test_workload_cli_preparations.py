"""CLI prepared listing/pickup dispatches saved setup without human login."""

import base64
from hashlib import sha256
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from atellagent_client.cli.main import _async_main
from atellagent_client.cli import setup_login
from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.proofs import canonical_json
from atellagent_client.onboarding.workload_context import WorkloadContext
from test_workload_management import draft, receipt


@pytest.fixture
def case(tmp_path, draft, monkeypatch):
    context = WorkloadContext(tmp_path / "state")
    context.setups.save_initial(draft.setup)
    settings = draft.proposal.payload(); settings.pop("id")
    keys = draft.setup.proposal()
    preparation = dict(id=str(draft.proposal.id), settings=settings, settings_sha256=sha256(canonical_json(settings)).hexdigest(),
        status="awaiting_proposal", proposed_request_id=None, expires_at="2026-10-08T12:00:00Z")
    page = dict(session_id=str(draft.setup.operation_id), tenant_id=str(draft.setup.tenant_id), operator_user_id=str(draft.setup.owner_user_id),
        proof_public_key_sha256=sha256(base64.urlsafe_b64decode(keys["proof_public_key_raw"] + "=")).hexdigest(),
        tls_public_key_sha256=sha256(base64.b64decode(keys["tls_public_key_der_base64"])).hexdigest(),
        preparations=[preparation], next_after=None)
    calls = []
    def respond(request):
        calls.append(request)
        assert "authorization" not in request.headers
        return httpx.Response(200, json=page if request.method == "GET" else receipt(draft))
    factory = httpx.AsyncClient
    monkeypatch.setattr("atellagent_client.onboarding.workload_preparation_client.httpx.AsyncClient",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(respond)))
    login = AsyncMock(side_effect=AssertionError("Listing/pickup must not login"))
    monkeypatch.setattr(setup_login, "login", login)
    return context, draft, page, calls, login


@pytest.mark.asyncio
async def test_cli_listing_then_pickup_pins_receipt_and_never_starts(case, capsys):
    context, draft, _, calls, login = case
    args = [str(draft.setup.operation_id), "--state-directory", str(context.directory)]
    await _async_main(["workload", "preparations", *args, "--json"])
    listing = json.loads(capsys.readouterr().out)
    assert listing["preparations"][0]["id"] == str(draft.proposal.id)
    assert "PRIVATE KEY" not in str(listing)
    await _async_main(["workload", "pickup", str(draft.setup.operation_id), str(draft.proposal.id),
        "--state-directory", str(context.directory)])
    assert context.receipts.load(draft).id == draft.proposal.id
    assert [request.method for request in calls] == ["GET", "GET", "POST"]
    assert "does not start" in capsys.readouterr().out
    login.assert_not_awaited()


@pytest.mark.asyncio
async def test_cli_missing_preparation_does_not_generate_proposal(case):
    context, draft, page, calls, _ = case
    page["preparations"] = []
    with pytest.raises(OnboardingClientError, match="not_on_page"):
        await _async_main(["workload", "pickup", str(draft.setup.operation_id), str(draft.proposal.id),
            "--state-directory", str(context.directory)])
    assert [request.method for request in calls] == ["GET"]
    with pytest.raises(OnboardingClientError): context.drafts.load(draft.proposal.id)
